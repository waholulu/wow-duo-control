import json
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from combat_evidence import CombatEvidence
from runtime_types import Snapshot, Result, Context, Cancelled, GuardFailed, Intent
from runtime_engine import Scheduler, Executor
from target_continuity import TargetContinuity
from test_support import make_controller, Driver
from vision_state import Observation


def sample(sequence, hp=1, xp=False, track='1', at=None, **kwargs):
    o=Observation(valid=True,player_hp=1,player_mana=1,target=track is not None,
                  target_allowed=track is not None,target_hp=hp,xp_visible=xp,
                  in_combat=track is not None)
    return Snapshot(sequence,sequence if at is None else at,(1,0,0),o,None,
                    target_track=track,**kwargs)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows=[]
        self.e=CombatEvidence(lambda kind,**kw:self.rows.append(dict(kind=kind,**kw)))

    def attack(self):
        self.e.observe(sample(1))
        self.e.acknowledge_attack(sample(1),'0001-combat',1.1)

    def test_skipped_damage_and_xp_frames_survive_without_policy_reads(self):
        self.attack()
        for s in [sample(2,.5),sample(3,0,True,None),sample(4,0,False,None)]:self.e.observe(s)
        self.assertEqual(self.e.facts()['xp_event_ids'],['xp:3'])
        self.assertEqual(self.e.facts('0001-combat')['xp_events'],1)
        self.assertEqual(len(self.rows),1)

    def test_no_ack_no_credit(self):
        for s in [sample(1),sample(2,.4),sample(3,0,True,None)]:self.e.observe(s)
        self.assertEqual(self.e.facts()['xp_events'],0)

    def test_ack_after_effect_recovers_retained_frames(self):
        for s in [sample(1),sample(2,.4),sample(3,0,True,None)]:self.e.observe(s)
        self.e.acknowledge_attack(sample(1),'0001-combat',1.1)
        self.assertEqual(self.e.facts()['xp_events'],1)

    def test_effect_before_actual_write_cannot_confirm_attack(self):
        for s in [sample(1),sample(2,.4),sample(3,0,True,None)]:self.e.observe(s)
        self.e.acknowledge_attack(sample(1),'0001-combat',3.1)
        self.assertEqual(self.e.facts()['xp_events'],0)

    def test_xp_without_damage_cannot_count(self):
        self.attack();self.e.observe(sample(2,1,True))
        self.assertEqual(self.e.facts()['xp_events'],0)

    def test_late_xp_after_skill_exit_keeps_original_owner(self):
        self.attack();self.e.observe(sample(2,.4))
        self.assertEqual(self.e.facts('0001-combat')['xp_events'],0)
        # No active skill is necessary for the capture-side confirmation.
        self.e.observe(sample(3,0,True,None))
        self.assertEqual(self.e.facts('0001-combat')['xp_event_ids'],['xp:3'])

    def test_duplicate_and_persistent_xp_never_recounts_even_in_defense(self):
        self.attack();self.e.observe(sample(2,.4,True))
        self.e.observe(sample(2,.4,True));self.e.observe(sample(3,.3,True))
        self.e.acknowledge_attack(sample(3,.3),'0002-defend',3.1)
        self.e.observe(sample(4,.2));self.e.observe(sample(5,.1,True))
        self.assertEqual(self.e.facts()['xp_events'],1)

    def test_changed_target_or_calibration_invalidates_association(self):
        for change in [sample(3,.3,track='2'),sample(3,.3,calibration_generation=2)]:
            with self.subTest(change=change):
                self.setUp();self.attack();self.e.observe(sample(2,.4));self.e.observe(change)
                self.e.observe(sample(4,0,True,None))
                self.assertEqual(self.e.facts()['xp_events'],0)

    def test_old_damage_does_not_credit_unrelated_xp(self):
        self.attack();self.e.observe(sample(2,.4));self.e.observe(sample(20,0,True,None))
        self.assertEqual(self.e.facts()['xp_events'],0)

    def test_overflow_is_explicit_and_preserves_previous_count(self):
        self.e.capacity=1;self.attack();self.e.observe(sample(2,.4,True))
        self.e.observe(sample(3,1,track='2'))
        self.e.acknowledge_attack(sample(3,1,track='2'),'0002-combat',3.1)
        with self.assertRaisesRegex(GuardFailed,'capacity'):
            self.e.observe(sample(4,.4,True,track='2'))
        self.assertEqual(self.e.facts()['xp_events'],1)


class ContinuityTests(unittest.TestCase):
    def test_explicit_template_alias_does_not_switch_entity(self):
        t=TargetContinuity({'a':['狗头人歹徒'],'b':['狗头人歹徒']})
        o=sample(1,.6).observation
        self.assertEqual(t.update(o,'a',(1,0,0),1,1),t.update(o,'b',(1,0,0),1,1.2))
        # Same name is insufficient if HP jumps to a fresh entity.
        changed=t.update(replace(o,target_hp=1),'b',(1,0,0),1,1.4)
        self.assertEqual(changed,'2')

    def test_different_or_unmapped_names_still_change_track(self):
        for aliases in ({},{'a':['wolf'],'b':['boar']}):
            t=TargetContinuity(aliases);o=sample(1).observation
            self.assertNotEqual(t.update(o,'a',(),0,1),t.update(o,'b',(),0,1.1))


class IntegrationTests(unittest.TestCase):
    def test_executor_registers_ack_and_recovers_effect_before_ack(self):
        e=CombatEvidence(lambda *a,**kw:None)
        initial=sample(1,at=time.monotonic());e.observe(initial)
        source=SimpleNamespace(peek=lambda:initial,combat_evidence=e)
        class Box:
            def tap(self,key,ms,admission):
                with admission() as written:written(1,True)
                e.observe(sample(2,.4,at=time.monotonic()))
                e.observe(sample(3,0,True,None,at=time.monotonic()))
                return 'ack'
            def close(self):pass
        executor=Executor(Box(),source,lambda *a,**kw:None)
        try:
            intent=Intent('tap',(30,80),'attack',initial,'0001-combat',executor.epoch,initial.captured_at+.5)
            self.assertTrue(executor.submit(intent).result(1)['acknowledged'])
            self.assertEqual(e.facts()['xp_event_ids'],['xp:3'])
        finally:executor.close()

    def test_combat_uses_journal_even_when_current_xp_boolean_is_false(self):
        from runtime_skills import combat
        d=Driver();e=CombatEvidence(lambda *a,**kw:None)
        e.observe(sample(1));e.acknowledge_attack(sample(1),d.ctx.task,1.1)
        e.observe(sample(2,.4));e.observe(sample(3,0,True,None))
        scheduler=SimpleNamespace(source=SimpleNamespace(combat_evidence=e),combat_options={})
        result=d.run(combat(d.ctx,scheduler))
        self.assertEqual(result.reason,'xp_limit_out_of_combat')
        self.assertEqual(result.facts['xp_events'],1)
        self.assertGreaterEqual(d.now,3)
        self.assertFalse(any(a.reason in ('attack','select_or_scan') for a in d.actions))


class DeadlineTests(unittest.TestCase):
    def test_run_deadline_is_distinct_from_skill_timeout(self):
        scheduler=Scheduler.__new__(Scheduler)
        scheduler.source=SimpleNamespace(death_review_pending=False)
        scheduler._stopped=lambda:False;scheduler.clock=lambda:10
        scheduler.store=SimpleNamespace(error=None)
        ctx=Context('0001-combat',10)
        with self.assertRaisesRegex(GuardFailed,'skill_deadline'):scheduler._check_active(ctx)
        ctx.run_deadline=10
        with self.assertRaisesRegex(Cancelled,'run_deadline'):scheduler._check_active(ctx)
        scheduler.store.error='disk_full'
        with self.assertRaisesRegex(GuardFailed,'evidence_writer_failed'):scheduler._check_active(ctx)

    def test_insufficient_remaining_budget_never_starts_another_pull(self):
        c=make_controller(['--no-loot']);c.deadline=110
        with patch('runtime_main.time.monotonic',return_value=100),patch.object(c,'run_skill') as run, \
                patch.object(c,'wind_down',return_value=Result('cancelled','run_deadline')) as finish:
            self.assertEqual(c.patrol().reason,'run_deadline')
            run.assert_not_called();finish.assert_called_once()

    def test_completed_run_requires_terminal_peace(self):
        c=make_controller(['--no-loot']);c.s.clock=lambda:10;c.store.emit=lambda *a,**kw:None
        for safety,expected in [('peace_confirmed','completed'),('observation_unavailable','failed')]:
            r=Result('completed' if expected=='completed' else 'failed',safety,{'safety_exit':safety})
            with patch('runtime_safety.resolve_threat',return_value=r):
                self.assertEqual(c.ensure_safe_exit(Result('completed','KILL_LIMIT_COMPLETE')).status,expected)

    def test_late_ledger_count_replaces_not_adds_to_controller_total(self):
        c=make_controller(['--no-loot']);c.kills=1;c.s.clock=lambda:10;c.store.emit=lambda *a,**kw:None
        c.s.source=SimpleNamespace(combat_evidence=SimpleNamespace(facts=lambda:dict(xp_events=2,xp_event_ids=['xp:2','xp:4'])))
        with patch('runtime_safety.resolve_threat',return_value=Result('completed','peace_confirmed',{'safety_exit':'peace_confirmed'})):
            result=c.ensure_safe_exit(Result('failed','opener_not_confirmed'))
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.facts['confirmed_kills'],2)
        self.assertEqual(c.kills,2)


class JournalRateTests(unittest.TestCase):
    def test_absolute_time_and_late_kill_use_original_capture_window(self):
        from rate_evidence import analyze
        with TemporaryDirectory() as directory:
            folder=Path(directory);base=1000
            rows=[dict(kind='combat_evidence_started',at=base)]
            ids=[]
            for n in range(10):
                at=base+(n//2)*60+10+(n%2)*20;task=f'{n:04d}-combat';identity=f'xp:{n+1}';ids.append(identity)
                rows.extend([dict(kind='skill_started',at=at-2,task=task),
                             dict(kind='skill_finished',at=at-1,task=task,result={'facts':{'xp_events':0,'xp_event_ids':[]}}),
                             dict(kind='observation',at=at+.1,captured_at=at,frame=n+1,observation={'xp_visible':True}),
                             dict(kind='kill_confirmed',at=at+2,captured_at=at,frame=n+1,task=task,xp_event_id=identity)])
            rows.append(dict(kind='end',at=base+305))
            (folder/'run.json').write_text(json.dumps({'started_monotonic':base}))
            (folder/'result.json').write_text(json.dumps(dict(status='cancelled',reason='run_deadline',facts=dict(confirmed_kills=10,xp_event_ids=ids,safety_exit='peace_confirmed'))))
            def report():
                (folder/'events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                return analyze(folder)
            r=report();self.assertEqual(r['kills_per_minute'],[2]*5);self.assertTrue(r['rate_met'])
            result=json.loads((folder/'result.json').read_text())
            result['facts']['uncertain_transaction']=True
            (folder/'result.json').write_text(json.dumps(result))
            self.assertFalse(report()['rate_met'])
            result['facts'].pop('uncertain_transaction')
            (folder/'result.json').write_text(json.dumps(result))
            rows.append(rows[-2])
            self.assertFalse(report()['rate_met'])


if __name__=='__main__':unittest.main()
