"""Offline regressions for the cross-class audit; no live devices."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from control_policy import Policy
from class_profiles import load_class,policy_options
from vision_state import Observation
from combat_log_cv import CombatLogReader
from run_permission import permission_issues
from death_review import require_review
from runtime_engine import Executor,Scheduler
from runtime_types import Intent,Result,GuardFailed
from test_runtime_engine import Source,Box,Store


def obs(**kw):
    values=dict(valid=True,player_hp=1,player_mana=1,target=True,target_allowed=True,
                target_hp=1,bearing=0,opener_in_range=True)
    values.update(kw)
    return Observation(**values)


class CombatAuditPolicyTests(unittest.TestCase):
    def test_unseen_target_under_threat_is_not_cleared(self):
        p=Policy();p.last_attempt=0;p.fight_started=0
        self.assertIsNone(p.step(obs(in_combat=True,bearing=None),8,0))
        self.assertEqual(p.stopped,'unseen_target_under_threat')

    def test_unseen_target_may_be_cleared_only_while_peaceful(self):
        p=Policy();p.last_attempt=0;p.fight_started=0
        self.assertEqual(p.step(obs(bearing=None),8,0).reason,'skip_unseen_target')

    def test_unseen_clear_is_rechecked_at_actual_input(self):
        source=Source();source.observation=obs();box=Box()
        executor=Executor(box,source,lambda *a,**kw:None)
        try:
            s=source.peek()
            intent=Intent('tap',(41,80),'skip_unseen_target',s,'combat',executor.epoch,s.captured_at+.5)
            source.observation=obs(in_combat=True)
            with self.assertRaisesRegex(GuardFailed,'unseen_skip_guard_changed'):executor.submit(intent).result(1)
            self.assertEqual(box.commands,[])
        finally:executor.close()

    def test_no_damage_timeout_remains_active_during_low_mana(self):
        p=Policy(attack_once=True,interact_key=65)
        p.attack_started=p.damaged_target=True;p.fight_started=0;p.last_progress=0;p.last_hp=.5
        p.step(obs(in_combat=True,target_hp=.5,player_mana=.1),1,0)
        self.assertEqual(p.state,'WAIT_MANA_COMBAT')
        p.step(obs(in_combat=True,target_hp=.5,player_mana=.1),11,0)
        self.assertEqual(p.stopped,'no_damage_in_combat')

    def test_elapsed_fight_timeout_is_not_paused_by_resource_or_cast_wait(self):
        for casting in (False,True):
            p=Policy();p.fight_started=0;p.last_progress=45;p.damaged_target=True;p.last_hp=.5
            p.step(obs(target_hp=.5,player_mana=.1,in_combat=True,casting=casting),46,0)
            self.assertEqual(p.stopped,'fight_timeout')

    def test_leave_rest_does_not_fabricate_progress(self):
        p=Policy();p.state='WAIT_MANA_COMBAT';p.rest_started=1;p.fight_started=0;p.last_progress=2
        p.leave_rest(9)
        self.assertEqual((p.fight_started,p.last_progress),(0,2))

    def test_opener_cooldown_waits_before_approach_or_buff(self):
        p=Policy(**policy_options(load_class('paladin')));p.opener_last_at=0
        for now in (1,5,9):
            self.assertIsNone(p.step(obs(opener_in_range=False),now,0))
        self.assertFalse(p.attack_started);self.assertEqual(p.opener_approaches,0)
        self.assertEqual(p.step(obs(),10.3,0).reason,'maintain_required_buff')

    def test_normal_pull_under_threat_does_not_wait_for_opener_cooldown(self):
        p=Policy(opener_key=33,attack_once=True,interact_key=65);p.opener_last_at=0
        p.step(obs(in_combat=True),2,0)
        self.assertEqual(p.stopped,'opener_cooldown_under_threat')

    def test_associated_target_defense_can_melee_during_cooldown(self):
        p=Policy(opener_key=33,attack_once=True,interact_key=65);p.opener_last_at=0;p.defensive=True
        self.assertEqual(p.step(obs(in_combat=True),2,0).reason,'interact_target')
        self.assertFalse(p.opener_confirmed)

    def test_damage_alone_cannot_confirm_judgement(self):
        p=Policy(opener_key=33,attack_once=True,interact_key=65)
        p.step(obs(),0,0)
        self.assertIsNone(p.step(obs(target_hp=.7),1,0))
        p.step(obs(target_hp=.7),3.1,0)
        self.assertEqual(p.stopped,'opener_not_confirmed');self.assertFalse(p.attack_started)

    def test_lower_bound_mana_cannot_confirm_resource_spend(self):
        p=Policy(opener_key=33,attack_once=True,interact_key=65)
        p.step(obs(player_mana_lower_bound=True),0,0)
        p.step(obs(target_hp=.7,player_mana=.8),1,0)
        self.assertFalse(p.opener_confirmed)

    def test_fresh_personal_spell_log_can_confirm_but_old_or_other_spell_cannot(self):
        p=Policy(opener_key=33,attack_once=True,interact_key=65,opener_log_names=['正义审判'])
        p.step(obs(),10,0)
        p.observe_offensive_log([dict(kind='damage_text',text='你的近战攻击命中狼',captured_at=10.5),
                                dict(kind='damage_text',text='你的正义审判命中狼',captured_at=9)],11)
        self.assertFalse(p.opener_log_confirmed)
        p.observe_offensive_log([dict(kind='damage_text',text='你的正义审判命中狼',captured_at=10.5)],11)
        self.assertEqual(p.step(obs(target_hp=.7,player_mana_lower_bound=True),11,0).reason,'interact_target')
        self.assertEqual(p.last_opener_effect[2],'personal_spell_log')

    def test_repeat_judgement_has_the_same_effect_check(self):
        p=Policy(opener_key=33,attack_once=True,interact_key=65)
        p.step(obs(),0,0);p.step(obs(target_hp=.8,player_mana=.95),1,0)
        self.assertEqual(p.step(obs(target_hp=.6,player_mana=.95),10.3,0).reason,'cooldown_strike')
        p.step(obs(target_hp=.5,player_mana=.95),13.4,0)
        self.assertEqual(p.stopped,'cooldown_strike_not_confirmed')

    def test_rejected_opener_restores_all_pending_action_state(self):
        p=Policy(opener_key=33,attack_once=True,interact_key=65)
        checkpoint=p.action_checkpoint();p.step(obs(),1,0);p.reject_action(checkpoint)
        self.assertIsNone(p.opener_pending_at);self.assertIsNone(p.opener_pending_mana)
        self.assertEqual(p.step(obs(),1.2,0).reason,'ranged_opener')

    def test_unknown_cast_waits_for_effect_and_never_walks_through_it(self):
        p=Policy();self.assertEqual(p.step(obs(),0,0).reason,'attack')
        for now in (2.6,4,6):self.assertIsNone(p.step(obs(),now,0))
        p.step(obs(),6.1,0)
        self.assertEqual(p.stopped,'cast_effect_unconfirmed')

    def test_unknown_cast_can_use_resource_and_target_changes(self):
        p=Policy();p.step(obs(),0,0)
        self.assertIsNone(p.step(obs(target_hp=.8,player_mana=.9),1,0))
        self.assertEqual(p.last_cast_effect[1],'resource_and_target_change')
        self.assertEqual(p.step(obs(target_hp=.8,player_mana=.9),2.7,0).reason,'attack')

    def test_known_cast_pushback_blocks_repeat_until_observed_end(self):
        p=Policy();p.step(obs(casting_known=True),0,0)
        for now in (1,2.7,4):self.assertIsNone(p.step(obs(casting_known=True,casting=True,player_mana=.9),now,0))
        self.assertIsNone(p.step(obs(casting_known=True,player_mana=.9),4.1,0))
        self.assertEqual(p.last_cast_effect[1],'cast_end_and_resource_change')
        self.assertEqual(p.step(obs(casting_known=True,target_hp=.8,player_mana=.9),4.3,0).reason,'attack')

    def test_cast_damage_without_resource_change_is_not_success(self):
        p=Policy();p.step(obs(),0,0);p.step(obs(target_hp=.5),1,0)
        p.step(obs(target_hp=.5),6.1,0)
        self.assertEqual(p.stopped,'cast_effect_unconfirmed')

    def test_threat_history_is_updated_even_while_waiting_for_cast_effect(self):
        p=Policy();p.step(obs(),0,0)
        p.step(obs(in_combat=True),1,0)
        self.assertEqual(p.last_combat_at,1)
        p.step(obs(target=False,target_allowed=False,in_combat=False),1.2,0)
        self.assertEqual(p.stopped,'target_lost_under_threat')


class LogOccurrenceTests(unittest.TestCase):
    def reader(self,windows):
        reader=CombatLogReader([0,0,1,1],lambda *a,**kw:None)
        self.addCleanup(reader.close)
        with patch('combat_log_cv.read_lines',side_effect=windows):
            for n in range(len(windows)):reader.process(None,n,n)
        return reader

    def test_same_incoming_line_can_recur_after_leaving_window(self):
        hit='苦力命中你5物理'
        reader=self.reader([['旧记录'],[hit],['另一条记录'],[hit]])
        self.assertEqual([r['captured_at'] for r in reader.latest(3,3,[])],[3])

    def test_appended_identical_occurrence_is_a_new_event(self):
        hit='苦力命中你5物理'
        reader=self.reader([[hit],[hit,hit]])
        rows=reader.latest(1,1,[])
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['transition_confirmed'])

    def test_static_or_temporarily_unreadable_text_is_not_new(self):
        hit='苦力命中你5物理'
        reader=self.reader([[hit],[],[hit],[hit]])
        self.assertEqual(reader.latest(3,0,[]),[])

    def test_scroll_overlap_preserves_repeated_personal_damage(self):
        hit='你的正义审判命中狼5神圣'
        reader=self.reader([[hit,'中间行'],['中间行',hit]])
        self.assertEqual(len(reader.latest(1,1,['狼'])),1)

    def test_unanchored_text_cannot_authorize_offensive_confirmation(self):
        reader=self.reader([['旧记录'],['你的正义审判命中狼5神圣']])
        self.assertEqual(reader.latest(1,1,['狼']),[])

    def test_stale_tab_worker_cannot_restore_old_evidence(self):
        reader=CombatLogReader([0,0,1,1],lambda *a,**kw:None);self.addCleanup(reader.close)
        reader.generation=2
        with patch('combat_log_cv.read_lines',return_value=['你已死亡']):reader.process(None,1,1,generation=1)
        self.assertEqual(reader.latest(1,0,[]),[]);self.assertTrue(reader.initial)


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.limits=dict(kills=1,max_seconds=180)
        self.write('runs/run-permissions/paladin.json',dict(character='paladin',sources=['progress.json'],
                   review_file='review.json',required_effect='backpack_open_read_close',blocked_since_run='blocked'))
        self.write('blocked/run.json',dict(started_wall_time=10))
        self.write('progress.json',dict(resume_allowed=False))
        self.review=dict(resume_allowed=True,evidence=['frames'],findings=['fixed'],fixes=['effect'],verification=['checked'],
                         resume_conclusion='one bounded trial',trial_scope=dict(tasks=['patrol'],max_kills=1,max_seconds=180))
        self.write('review.json',self.review)

    def write(self,path,data):
        file=self.root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(json.dumps(data))

    def issues(self,task='patrol'):
        return permission_issues(self.root,'paladin',task,self.limits,required=True)

    def proof(self):
        self.write('input/run.json',dict(started_wall_time=20))
        self.write('input/class-profile.json',dict(**{'class':'paladin'}))
        self.write('input/result.json',dict(status='completed',reason='backpack_confirmed',
                   facts=dict(closed_verified=True),evidence=[4,6]))
        rows=[dict(kind='action_acknowledged',task='bag',reason='open_backpack')]
        rows += [dict(kind='backpack_reading',task='bag',frame=n,reading=dict(valid=True,open=True)) for n in (2,3,4)]
        rows += [dict(kind='action_acknowledged',task='bag',reason='close_backpack')]
        (self.root/'input/events.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
        self.review['input_effect_run']='input';self.write('review.json',self.review)

    def test_latest_pause_overrides_old_permission(self):
        self.assertTrue(any('run_resume_blocked' in v for v in self.issues()))

    def test_recovery_and_observation_remain_available(self):
        for task in ('recover','escape','observe','bag'):self.assertEqual(self.issues(task),[])

    def test_resume_boolean_alone_cannot_release_input_hold(self):
        self.write('progress.json',dict(resume_allowed=True))
        self.assertIn('pc_input_effect_unconfirmed',self.issues())

    def test_full_review_and_effect_proof_allow_only_bounded_scope(self):
        self.proof();self.write('progress.json',dict(resume_allowed=True))
        self.assertEqual(self.issues(),[])
        self.limits['kills']=2;self.assertIn('run_trial_scope_required',self.issues())

    def test_new_permission_cannot_override_unresolved_death(self):
        self.proof();self.write('progress.json',dict(resume_allowed=True))
        require_review(self.root,'paladin','run','zero_hp')
        self.assertTrue(any('death_review_required' in v for v in self.issues()))

    def test_old_input_proof_cannot_release_a_newer_failure(self):
        self.proof();self.write('progress.json',dict(resume_allowed=True))
        self.write('input/run.json',dict(started_wall_time=9))
        self.assertIn('pc_input_effect_unconfirmed',self.issues())

    def test_acknowledgement_without_visual_effect_is_rejected(self):
        self.proof();self.write('progress.json',dict(resume_allowed=True))
        self.write('input/result.json',dict(status='failed',reason='backpack_unconfirmed'))
        self.assertIn('pc_input_effect_unconfirmed',self.issues())

    def test_missing_required_state_and_corrupt_sources_fail_closed(self):
        (self.root/'progress.json').write_text('{broken')
        self.assertTrue(any('unreadable' in v for v in self.issues()))
        (self.root/'runs/run-permissions/paladin.json').unlink()
        self.assertTrue(any('missing' in v for v in self.issues()))

    def test_existing_paladin_pause_is_migrated_without_releasing_it(self):
        root=Path(__file__).resolve().parent
        self.assertTrue(permission_issues(root,'paladin','patrol',self.limits,required=True))

    def test_scheduler_rechecks_permission_before_combat_factory(self):
        source=Source();box=Box();store=Store(self.root);executor=Executor(box,source,store.emit)
        scheduler=Scheduler(source,executor,store)
        scheduler.combat_permission=lambda name:['paused'] if name=='combat' else []
        called=[]
        def factory(ctx):
            called.append(True)
            if False:yield
            return Result('completed','unexpected')
        try:
            result=scheduler.run('combat',factory,1,False,False)
            self.assertEqual(result.reason,'paused');self.assertEqual(called,[]);self.assertEqual(box.commands,[])
        finally:scheduler.close();executor.close()
