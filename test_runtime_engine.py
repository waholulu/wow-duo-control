import contextlib
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from vision_state import Observation
from runtime_types import Snapshot, Intent, Result, Observe, Wait, Work, GuardFailed, Cancelled
from runtime_engine import Executor, Scheduler


class Source:
    def __init__(self):
        self.sequence=0
        self.calibration=(1.,0.,0.)
        self.generation=0
        self.target_track=None
        self.observation=Observation(valid=True,player_hp=1,player_mana=1)
        self.minerals=[]

    def peek(self):
        self.sequence+=1
        return Snapshot(self.sequence,time.monotonic(),self.calibration,self.observation,None,
                        calibration_generation=self.generation,target_track=self.target_track)


class Store:
    def __init__(self,folder):
        self.folder=Path(folder)
        self.error=None
        self.rows=[]

    def emit(self,kind,**values):
        self.rows.append(dict(kind=kind,**values))

    def clip(self,reason):
        self.emit('clip',reason=reason)


class Box:
    def __init__(self):
        self.commands=[]
        self.written=threading.Event()
        self.block=None
        self.closed=False

    def command(self,value,admission=None):
        with admission() if admission else contextlib.nullcontext() as written:
            self.commands.append(value)
            self.written.set()
            if written:
                written(1,True)
        if self.block:
            self.block.wait(2)
        return 'ack'

    def tap(self,key,ms,admission=None):
        return self.command((key,ms),admission)

    def close(self):
        self.closed=True


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.source=Source()
        self.box=Box()
        self.rows=[]
        self.executor=Executor(self.box,self.source,lambda kind,**kw:self.rows.append(dict(kind=kind,**kw)))
        self.addCleanup(self.executor.close)

    def intent(self,**kw):
        s=self.source.peek()
        return replace(Intent('tap',(26,80),'test',s,'task',self.executor.epoch,s.captured_at+.5),**kw)

    def test_actual_write_rechecks_age_after_queued_ack(self):
        self.box.block=threading.Event()
        first=self.executor.submit(self.intent())
        self.assertTrue(self.box.written.wait(.5))
        second=self.executor.submit(self.intent(expires_at=time.monotonic()+.02))
        time.sleep(.04)
        self.box.block.set()
        first.result(1)
        with self.assertRaises(GuardFailed):second.result(1)
        self.assertEqual(len(self.box.commands),1)

    def test_death_review_latches_even_after_valid_frame_returns(self):
        self.source.death_review_pending=True
        with self.assertRaisesRegex(GuardFailed,'death_review_required'):
            self.executor.check(self.intent(task='0002-combat'))
        self.assertEqual(self.box.commands,[])

    def test_cancel_does_not_wait_for_device_ack(self):
        self.box.block=threading.Event()
        first=self.executor.submit(self.intent())
        self.assertTrue(self.box.written.wait(.5))
        second=self.executor.submit(self.intent())
        started=time.monotonic()
        self.executor.revoke()
        self.assertLess(time.monotonic()-started,.1)
        self.box.block.set()
        first.result(1)
        with self.assertRaises(Cancelled):second.result(1)
        self.assertEqual(len(self.box.commands),1)

    def test_calibration_change_rejects_pending_mouse(self):
        intent=self.intent(kind='move',values=(10,10))
        self.source.calibration=(.7,100.,50.)
        with self.assertRaises(GuardFailed):self.executor.submit(intent).result(1)
        self.assertEqual(self.box.commands,[])

    def test_future_and_old_frame_rejected(self):
        for delta in (-1,1):
            intent=self.intent()
            intent=replace(intent,snapshot=replace(intent.snapshot,captured_at=time.monotonic()+delta))
            with self.assertRaises(GuardFailed):self.executor.submit(intent).result(1)
        self.assertEqual(self.box.commands,[])

    def test_combat_guard_uses_latest_frame(self):
        intent=self.intent(peaceful=True)
        self.source.observation.in_combat=True
        with self.assertRaises(GuardFailed):self.executor.submit(intent).result(1)
        self.assertEqual(self.box.commands,[])

    def test_anisotropic_mouse_mapping(self):
        self.source.calibration=(.7,1.2,100.,50.)
        self.executor.submit(self.intent(kind='move',values=(20,-20))).result(1)
        self.assertEqual(self.box.commands,['km.move(14,-24)'])

    def test_small_pointer_step_does_not_round_to_no_movement(self):
        self.source.calibration=(.5,100.,50.)
        self.executor.submit(self.intent(kind='move',values=(1,-1))).result(1)
        self.assertEqual(self.box.commands,['km.move(1,-1)'])

    def test_absolute_pointer_reacquisition_is_bounded_and_restores_relative_mode(self):
        self.executor.submit(self.intent(kind='move_to',values=(609,347,1920,1080),
                                         reason='reacquire_pointer_absolute',peaceful=True)).result(1)
        self.assertEqual(self.box.commands,
                         ['km.Screen(1920,1080);km.zero(1);km.moveto(609,347);km.zero(0)'])

    def test_absolute_pointer_reacquisition_rejects_out_of_bounds_destination(self):
        with self.assertRaisesRegex(ValueError,'Absolute mouse destination'):
            self.executor.submit(self.intent(kind='move_to',values=(1920,347,1920,1080),
                                             peaceful=True)).result(1)
        self.assertEqual(self.box.commands,[])

    def test_key_duration_and_nonfinite_health_rejected_before_transport(self):
        with self.assertRaises(ValueError):
            self.executor.submit(self.intent(values=(26,501))).result(1)
        self.source.observation.player_hp=float('nan')
        with self.assertRaisesRegex(GuardFailed,'invalid_player_state'):
            self.executor.submit(self.intent()).result(1)
        self.assertEqual(self.box.commands,[])

    def test_target_change_revokes_attack_even_when_frame_is_fresh(self):
        self.source.observation.target=True
        self.source.observation.target_allowed=True
        intent=self.intent(reason='attack')
        self.source.observation.target_allowed=False
        with self.assertRaisesRegex(GuardFailed,'target_changed'):
            self.executor.submit(intent).result(1)
        self.assertEqual(self.box.commands,[])

    def test_logs_distinguish_proposal_and_ack_without_claiming_effect(self):
        self.executor.submit(self.intent()).result(1)
        self.assertEqual([r['kind'] for r in self.rows],['action_proposed','action_sent','action_acknowledged'])

    def test_same_transform_with_new_calibration_generation_rejects_input(self):
        intent=self.intent(kind='move',values=(10,10))
        self.source.generation+=1
        with self.assertRaisesRegex(GuardFailed,'calibration_changed'):
            self.executor.submit(intent).result(1)
        self.assertEqual(self.box.commands,[])

    def test_allowed_target_replacement_rejects_attack(self):
        self.source.observation.target=self.source.observation.target_allowed=True
        self.source.target_track='old'
        intent=self.intent(reason='attack')
        self.source.target_track='new'
        with self.assertRaisesRegex(GuardFailed,'target_changed'):
            self.executor.submit(intent).result(1)
        self.assertEqual(self.box.commands,[])

    def test_target_bound_defense_cannot_tab_to_another_target(self):
        self.source.target_track='other'
        with self.assertRaisesRegex(GuardFailed,'no_longer_associated'):
            self.executor.submit(self.intent(reason='select_or_scan',required_target='attacker')).result(1)
        self.assertEqual(self.box.commands,[])

    def test_evidence_guard_rechecks_at_write_after_device_wait(self):
        allowed=[True]
        original=self.box.command
        def delayed(value,admission=None):
            allowed[0]=False
            return original(value,admission)
        self.box.command=delayed
        with self.assertRaisesRegex(GuardFailed,'evidence_invalidated'):
            self.executor.submit(self.intent(admission_guard=lambda:allowed[0])).result(1)
        self.assertEqual(self.box.commands,[])

    def test_same_epoch_cannot_be_submitted_as_other_active_task(self):
        self.executor.revoke('active')
        with self.assertRaises(Cancelled):
            self.executor.submit(self.intent(task='other')).result(1)
        self.assertEqual(self.box.commands,[])

    def test_write_failure_is_not_logged_as_sent_or_acknowledged(self):
        def fail(value,admission=None):
            with admission():
                raise OSError('disconnected before write')
        self.box.command=fail
        with self.assertRaises(OSError):
            self.executor.submit(self.intent()).result(1)
        self.assertEqual([r['kind'] for r in self.rows],['action_proposed','action_rejected'])

    def test_clock_is_rechecked_after_reading_live_source(self):
        now=[100.]
        self.executor.clock=lambda:now[0]
        snap=replace(self.source.peek(),captured_at=100.)
        def slow_peek():
            now[0]+=1
            return replace(snap,captured_at=now[0])
        self.source.peek=slow_peek
        intent=Intent('tap',(26,80),'test',snap,'task',self.executor.epoch,100.5)
        with self.assertRaisesRegex(GuardFailed,'stale_frame'):
            self.executor.submit(intent).result(1)
        self.assertEqual(self.box.commands,[])


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        self.source=Source()
        self.box=Box()
        self.executor=Executor(self.box,self.source,self.store.emit)
        self.scheduler=Scheduler(self.source,self.executor,self.store)
        self.addCleanup(self.executor.close)
        self.addCleanup(self.scheduler.close)

    def test_guard_failure_preserves_confirmed_xp(self):
        def skill(ctx):
            ctx.checkpoint['confirmed_facts']={'xp_events':1}
            yield Observe()
            raise GuardFailed('invalid_or_stale_perception')
        result=self.scheduler.run('combat',skill,1,False)
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.facts['xp_events'],1)
        self.assertEqual(self.box.commands,[])

    def test_stop_file_interrupts_wait_not_just_next_round(self):
        def skill(ctx):
            yield Wait(5)
            self.fail('Cancelled wait must not continue')
        timer=threading.Timer(.05,lambda:(self.store.folder/'STOP').touch())
        timer.start()
        started=time.monotonic()
        result=self.scheduler.run('wait',skill,10)
        timer.join()
        self.assertEqual(result.status,'cancelled')
        self.assertLess(time.monotonic()-started,.2)
        self.assertEqual(self.box.commands,[])

    def test_slow_ocr_does_not_block_cancellation(self):
        released=threading.Event()
        def skill(ctx):
            yield Work(lambda:released.wait(2))
            self.fail('OCR result must not revive cancelled skill')
        timer=threading.Timer(.06,self.scheduler.stop)
        timer.start()
        started=time.monotonic()
        result=self.scheduler.run('ocr',skill,5)
        released.set()
        timer.join()
        self.assertEqual(result.status,'cancelled')
        self.assertLess(time.monotonic()-started,.2)

    def test_epoch_changes_between_skills(self):
        epochs=[]
        def skill(ctx):
            epochs.append(ctx.epoch)
            s=yield from ctx.observe()
            yield from ctx.act(s,'tap',(26,20),'move')
            return Result('completed','done')
        for _ in range(2):
            self.assertEqual(self.scheduler.run('test',skill).status,'completed')
        self.assertLess(epochs[0],epochs[1])
        self.assertEqual(len(self.box.commands),2)

    def test_failed_stage_gets_terminal_result(self):
        def skill(ctx):
            yield Observe()
            raise ValueError('broken detector')
        result=self.scheduler.run('failure',skill)
        self.assertEqual(result.status,'failed')
        self.assertIn('broken detector',result.reason)
        self.assertTrue(any(r['kind']=='skill_finished' for r in self.store.rows))

    def test_skill_final_guard_reads_current_snapshot_after_transport_wait(self):
        self.source.target_track='approved_item'
        original=self.box.command
        def changed_before_write(value,admission=None):
            self.source.target_track='replacement_item'
            return original(value,admission)
        self.box.command=changed_before_write
        def skill(ctx):
            authorized=yield from ctx.observe()
            self.assertIsNotNone(ctx.current_snapshot)
            yield from ctx.act(authorized,'click',(1,),'sell_approved_junk',
                admission_guard=lambda:ctx.current_snapshot().target_track == authorized.target_track)
            return Result('completed','must_not_sell_replacement')
        result=self.scheduler.run('sale',skill,5)
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.reason,'skill_evidence_invalidated_before_input')
        self.assertEqual(self.box.commands,[])

    def test_unknown_attacker_escapes_instead_of_attacking(self):
        self.source.observation.in_combat=True
        called=[]
        def recovery(ctx):
            called.append(ctx.task)
            self.source.observation.in_combat=False
            yield Observe()
            return Result('completed','recovered')
        def original(ctx):
            yield Observe()
            return Result('completed','destination')
        with patch('runtime_skills.escape',recovery),patch('runtime_skills.recover',recovery),patch('runtime_skills.combat') as fight:
            result=self.scheduler.run('navigation',original,5)
        self.assertEqual(result.reason,'destination')
        self.assertTrue(any('escape' in name for name in called))
        fight.assert_not_called()

    def test_unconfirmed_sale_is_never_replayed_after_interrupt(self):
        def original(ctx):
            ctx.checkpoint['uncertain_transaction']=True
            self.source.observation.in_combat=True
            yield Observe()
            self.fail('Pending transaction cannot resume')
        def recover(ctx):
            self.source.observation.in_combat=False
            yield Observe()
            return Result('completed','recovered')
        with patch('runtime_skills.escape',recover),patch('runtime_skills.recover',recover):
            result=self.scheduler.run('sale',original,5)
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.reason,'recovery_failed_or_resume_unsafe')

    def test_recovery_inherits_parent_deadline(self):
        self.source.observation.in_combat=True
        deadlines=[]
        def recovery(ctx):
            deadlines.append(ctx.deadline)
            yield Wait(10)
        def original(ctx):
            deadlines.append(ctx.deadline)
            yield Observe()
        started=time.monotonic()
        with patch('runtime_skills.escape',recovery):
            result=self.scheduler.run('navigation',original,.12)
        self.assertEqual(result.reason,'skill_deadline')
        self.assertEqual(deadlines[0],deadlines[1])
        self.assertLess(time.monotonic()-started,.3)

    def test_repeated_threats_can_each_interrupt_and_resume(self):
        attempts=[]
        def original(ctx):
            attempts.append(ctx.epoch)
            self.source.observation.in_combat=len(attempts)<3
            yield Observe()
            return Result('completed','destination')
        def recovery(ctx):
            self.source.observation.in_combat=False
            yield Observe()
            return Result('completed','recovered')
        with patch('runtime_skills.escape',recovery),patch('runtime_skills.recover',recovery):
            result=self.scheduler.run('navigation',original,5)
        self.assertEqual(result.reason,'destination')
        self.assertEqual(len(attempts),3)
        self.assertEqual(len(set(attempts)),3)

    def test_previous_encounter_does_not_authorize_a_new_allowed_target(self):
        self.source.observation.in_combat=True
        self.source.observation.target=self.source.observation.target_allowed=True
        self.source.target_track='old'
        self.scheduler.note_engagement(self.source.peek())
        self.source.target_track='replacement'
        def recovery(ctx):
            self.source.observation.in_combat=False
            yield Observe()
            return Result('completed','recovered')
        def original(ctx):
            yield Observe()
            return Result('completed','destination')
        with patch('runtime_skills.escape',recovery),patch('runtime_skills.recover',recovery),patch('runtime_skills.combat') as fight:
            result=self.scheduler.run('navigation',original,5)
        self.assertEqual(result.reason,'destination')
        fight.assert_not_called()

    def test_same_associated_target_defense_is_bound_to_observed_track(self):
        self.source.observation.in_combat=True
        self.source.observation.target=self.source.observation.target_allowed=True
        self.source.target_track='attacker'
        self.scheduler.note_engagement(self.source.peek())
        tracks=[]
        def defend(ctx,*args):
            tracks.append(ctx.required_target)
            self.source.observation.in_combat=False
            yield Observe()
            return Result('completed','defended')
        def recovery(ctx):
            yield Observe()
            return Result('completed','recovered')
        def original(ctx):
            yield Observe()
            return Result('completed','destination')
        with patch('runtime_skills.combat',defend),patch('runtime_skills.recover',recovery),patch('runtime_skills.escape') as escape:
            result=self.scheduler.run('navigation',original,5)
        self.assertEqual(result.reason,'destination')
        self.assertEqual(tracks,['attacker'])
        escape.assert_not_called()

    def test_future_observation_cannot_complete_a_skill(self):
        snap=replace(self.source.peek(),captured_at=time.monotonic()+20)
        self.source.peek=lambda:snap
        def original(ctx):
            yield Observe()
            return Result('completed','should_not_complete')
        result=self.scheduler.run('test',original,5)
        self.assertEqual(result.reason,'invalid_or_stale_perception')

    def test_cancelled_ocr_workers_have_bounded_shutdown_and_explicit_failure(self):
        released=threading.Event()
        running=threading.Event()
        def work():
            running.set()
            released.wait(5)
        self.scheduler.workers.submit(work)
        self.assertTrue(running.wait(.2))
        started=time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError,'did not stop'):
                self.scheduler.close()
            self.assertLess(time.monotonic()-started,1.3)
            self.assertTrue(any(r['kind']=='recognition_shutdown_incomplete' for r in self.store.rows))
        finally:
            released.set()


if __name__=='__main__':unittest.main()
