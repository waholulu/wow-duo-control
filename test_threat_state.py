"""Offline threat transitions and real skill entry; never opens devices."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from combat_exit_watch import ExitWatch
from combat_log_cv import evidence
from control_policy import Policy
from runtime_skills import combat, escape, recover, select_chat
from runtime_types import Snapshot, GuardFailed
from test_support import Driver
from threat_state import ThreatMonitor
from vision_state import Observation


def frame(seq, at, hp=1, **kw):
    return Snapshot(seq, at, (), Observation(valid=True, player_hp=hp, player_mana=1, **kw), None)


def incoming(at, names=('狗头人劳工', '狗头人苦力'), confirmed=True):
    return [dict(row, captured_at=at, transition_confirmed=confirmed)
            for row in evidence([name+'的攻击命中你，造成5点伤害。' for name in names])]


class ThreatMonitorTests(unittest.TestCase):
    def test_small_hits_accumulate_but_jitter_does_not(self):
        m=ThreatMonitor()
        states=[m.update(frame(i,i*.25,hp),i*.25) for i,hp in enumerate((1,.99,.98,.97))]
        self.assertFalse(states[1].damaged)
        self.assertTrue(states[-1].damaged)
        m=ThreatMonitor()
        for i,hp in enumerate((1,.995,1,.99,1,.995)):
            self.assertFalse(m.update(frame(i,i*.25,hp),i*.25).damaged)

    def test_old_injury_does_not_authorize_new_movement(self):
        m=ThreatMonitor();m.update(frame(1,0),0)
        self.assertTrue(m.update(frame(2,1,.9),1).damaged)
        self.assertFalse(m.update(frame(3,1.5,.9),1.5).visual)

    def test_duplicate_and_stale_frames_cannot_create_peace(self):
        m=ThreatMonitor();s=frame(1,0)
        m.update(s,0)
        self.assertEqual(m.update(s,.4).quiet_frames,1)
        self.assertFalse(m.update(s,3).fresh)
        for i,t in enumerate((4,5,6),2):
            self.assertFalse(m.update(frame(i,t),t).peaceful())
        self.assertTrue(m.update(frame(5,7),7).peaceful())

    def test_only_confirmed_recent_distinct_source_hints_raise_suspicion(self):
        for rows in (incoming(0,confirmed=False),incoming(-4),incoming(1),
                     incoming(0,names=('同名怪','同名怪'))):
            self.assertFalse(ThreatMonitor().update(frame(1,0),0,rows).additional_suspected)
        s=ThreatMonitor().update(frame(1,0),0,incoming(0))
        self.assertTrue(s.additional_suspected)
        self.assertFalse(s.visual)
        self.assertTrue(s.incoming)

    def test_async_log_can_arrive_on_same_frame_without_extra_health_sample(self):
        m=ThreatMonitor();s=frame(1,0)
        m.update(s,0)
        state=m.update(s,.1,incoming(0))
        self.assertTrue(state.additional_suspected)
        self.assertEqual(len(m.samples),1)

    def test_previous_phase_injury_survives_transition_to_exit_watch(self):
        m=ThreatMonitor();m.update(frame(1,0,in_combat=True),0)
        s=frame(2,1,.90,in_combat=True);state=m.update(s,1)
        w=ExitWatch(1)
        self.assertEqual(w.step(s.observation,True,0,1,sequence=2,threat_state=state),'escape')

    def test_settled_encounter_does_not_keep_old_injury(self):
        m=ThreatMonitor();m.update(frame(1,0),0);m.update(frame(2,1,.9),1)
        for i,t in enumerate((2,3,4,5),3):m.update(frame(i,t,.9),t)
        s=m.update(frame(7,6,.89,in_combat=True),6)
        self.assertAlmostEqual(s.episode_drop,.01)

    def test_text_only_additional_sources_do_not_authorize_escape(self):
        w=ExitWatch(0);s=frame(1,10)
        state=ThreatMonitor().update(s,10,incoming(10))
        self.assertEqual(w.step(s.observation,True,0,10,sequence=1,threat_state=state),'observe')

    def test_invalid_configuration_rejected(self):
        for kwargs in ({'damage_drop':0},{'damage_drop':float('nan')},{'damage_window_seconds':30}):
            with self.assertRaises(ValueError):ThreatMonitor(**kwargs)


class ThreatSkillTests(unittest.TestCase):
    def test_actual_paladin_defense_enters_policy_with_persistent_panel(self):
        from class_profiles import load_class, policy_options
        d=Driver();d.ctx.vision_profile=json.loads(Path('classes/paladin/vision/profile.json').read_text())
        d.ctx.required_target='original'
        d.observation=Observation(valid=True,player_hp=.8,player_mana=1,in_combat=True,
                                  target=True,target_allowed=True,target_hp=.8,bearing=0,opener_in_range=True)
        scheduler=SimpleNamespace(combat_options=policy_options(load_class('paladin')),note_engagement=lambda _:None)
        # Only CV of the static panel is stubbed; real combat/policy/rotation runs.
        with patch('chat_tabs.selected',return_value=True):
            result=d.run(combat(d.ctx,scheduler,10))
        self.assertNotEqual(result.reason,'combat_or_unknown')
        self.assertTrue(any(a.reason=='ranged_opener' for a in d.actions))
        self.assertTrue(all(a.required_target=='original' for a in d.actions))
        self.assertNotIn('select_or_scan',[a.reason for a in d.actions])

    def test_nonpersistent_panel_still_cannot_be_clicked_in_combat(self):
        d=Driver();d.observation.in_combat=True
        d.ctx.vision_profile={'chat_tabs':{'combat':{'persistent':False}}}
        with self.assertRaisesRegex(GuardFailed,'combat_or_unknown'):
            d.run(select_chat(d.ctx,'combat'))
        self.assertEqual(d.actions,[])

    def test_additional_source_stops_combat_and_preserves_same_frame_xp(self):
        d=Driver();d.observation.in_combat=True;d.observation.xp_visible=True
        d.ctx.threat_log=SimpleNamespace(latest=lambda now,*_:incoming(now))
        p=Policy();p.pending_xp_until=10
        with patch('runtime_skills.Policy',return_value=p):
            result=d.run(combat(d.ctx,SimpleNamespace()))
        self.assertEqual(result.reason,'additional_attacker_suspected')
        self.assertEqual(result.facts['xp_events'],1)
        self.assertEqual(d.actions,[])

    def test_defense_kill_cleanup_is_not_bound_to_dead_target(self):
        d=Driver();d.ctx.required_target='dead';p=Policy();p.xp_events=1
        with patch('runtime_skills.Policy',return_value=p):
            result=d.run(combat(d.ctx,SimpleNamespace()))
        self.assertEqual(result.reason,'xp_limit_out_of_combat')
        self.assertTrue(d.actions)
        self.assertTrue(all(a.required_target is None for a in d.actions))
        self.assertEqual([a.reason for a in d.actions],['cancel_movement_after_kill'])

    def test_text_delays_escape_success_without_movement(self):
        d=Driver();d.ctx.threat_log=SimpleNamespace(latest=lambda now,*_:incoming(0) if now<=3 else [])
        result=d.run(escape(d.ctx))
        self.assertEqual(result.reason,'ESCAPED')
        self.assertGreaterEqual(d.now,8)
        self.assertEqual(d.actions,[])

    def test_recovery_detects_small_hits_with_missing_combat_icon(self):
        d=Driver();d.observation.player_hp=.9
        original=d.frame
        def dropping():
            d.observation.player_hp=.9-.01*d.sequence
            return original()
        d.frame=dropping
        result=d.run(recover(d.ctx))
        self.assertEqual(result.reason,'combat_restarted_during_recovery')
        self.assertLess(d.now,2)
        self.assertEqual(d.actions,[])


if __name__=='__main__':unittest.main()
