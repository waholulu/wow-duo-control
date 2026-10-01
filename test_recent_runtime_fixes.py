import unittest,time,threading
from types import SimpleNamespace
from dataclasses import replace
from runtime_engine import Executor
from runtime_types import Intent,Observe,GuardFailed
from runtime_skills import combat,tap
from test_runtime_engine import Source,Box
from test_support import Driver
from vision_state import Observation
from local_status_text import PercentReader
from unittest.mock import patch
import numpy as np
class RecentFixTests(unittest.TestCase):
 def test_rejected_action_keeps_facts_from_the_observed_frame(self):
  from control_policy import Policy
  p=Policy();p.pending_xp_until=10;p.last_hp=1
  before=p.action_checkpoint()
  observation=Observation(valid=True,player_hp=1,player_mana=1,target=True,
                          target_allowed=True,target_hp=.7,xp_visible=True,bearing=0)
  action=p.step(observation,1,0)
  self.assertEqual(action.reason,'attack')
  p.reject_action(before)
  self.assertEqual(p.xp_events,1)
  self.assertTrue(p.damaged_target)
  self.assertEqual(p.last_hp,.7)
  self.assertFalse(p.attack_started)

 def test_unsent_utility_tap_reacquires_after_stale_frame(self):
  from runtime_types import Wait
  d=Driver();records=[];d.ctx.record=lambda kind,**kw:records.append((kind,kw))
  generator=tap(d.ctx,26,80,'patrol_move',.95,True)
  first=next(generator);self.assertIsInstance(first,Observe)
  intent=generator.send(d.frame());self.assertIsInstance(intent,Intent)
  pause=generator.throw(GuardFailed('stale_frame_before_input'))
  self.assertIsInstance(pause,Wait)
  second=generator.send(None);self.assertIsInstance(second,Observe)
  self.assertTrue(any(kind=='tap_action_recomputed' for kind,_ in records))

 def test_post_kill_stop_releases_pending_ctm(self):
  source=Source();box=Box();e=Executor(box,source,lambda *a,**k:None)
  try:
   e.ctm_pending=True;s=source.peek();i=Intent('tap',(22,20),'cancel_movement_after_kill',s,'task',e.epoch,s.captured_at+.5)
   e.submit(i).result(1);self.assertFalse(e.ctm_pending)
  finally:e.close()
 def test_ocr_does_not_block_and_changed_pixels_do_not_use_old_result(self):
  r=PercentReader();gate=threading.Event();p=np.zeros((15,39,3),dtype=np.uint8);p[3:8,3:7]=255
  def recognition(a):gate.wait(1);r.pending_result=(a,.83)
  with patch.object(r,'recognize',side_effect=recognition):
   started=time.monotonic();self.assertIsNone(r.read(p));self.assertLess(time.monotonic()-started,.1)
   gate.set();r.worker.join(1)
   self.assertEqual(r.read(p),.83)
   self.assertIsNone(r.read(np.full_like(p,100)))
 def test_unsent_opener_recomputed_from_new_frame(self):
  d=Driver();d.observation=Observation(valid=True,player_hp=1,player_mana=1,target=True,target_allowed=True,target_hp=1,bearing=0,opener_in_range=True)
  scheduler=SimpleNamespace(combat_options=dict(attack_once=True,attack_key=30,interact_key=65,opener_key=33))
  g=combat(d.ctx,scheduler);op=next(g);self.assertIsInstance(op,Observe)
  first=g.send(d.frame());self.assertIsInstance(first,Intent);self.assertEqual(first.reason,'ranged_opener')
  op=g.throw(GuardFailed('stale_frame_before_input'));self.assertIsInstance(op,Observe)
  d.now+=.2;second=g.send(d.frame());self.assertEqual(second.reason,'ranged_opener');self.assertGreater(second.snapshot.sequence,first.snapshot.sequence)
  g.close()
 def test_healthy_ctm_waits_for_fresh_capture_without_new_input(self):
  from runtime_engine import Scheduler
  from runtime_types import Result
  from test_runtime_engine import Store
  import tempfile
  source=Source();base=source.peek;calls=[0]
  def delayed():
   calls[0]+=1;s=base()
   return replace(s,captured_at=s.captured_at-.6) if calls[0]<4 else s
  source.peek=delayed;box=Box()
  with tempfile.TemporaryDirectory() as d:
   store=Store(d);e=Executor(box,source,store.emit);e.ctm_pending=True;s=Scheduler(source,e,store)
   def observe(ctx):
    yield from ctx.observe()
    return Result('completed','fresh_observation')
   try:
    r=s.run('combat',observe,2,False,False)
    self.assertEqual(r.reason,'fresh_observation')
    self.assertFalse(any(x.get('kind')=='action_sent' for x in store.rows))
   finally:s.close();e.close()
 def test_healthy_read_only_startup_waits_through_one_point_six_second_frame(self):
  from runtime_engine import Scheduler
  from runtime_types import Result
  from test_runtime_engine import Store
  import tempfile
  source=Source();base=source.peek;calls=[0]
  def delayed():
   calls[0]+=1;s=base()
   return replace(s,captured_at=s.captured_at-1.6) if calls[0]<4 else s
  source.peek=delayed;box=Box()
  with tempfile.TemporaryDirectory() as d:
   store=Store(d);e=Executor(box,source,store.emit);scheduler=Scheduler(source,e,store)
   def observe(ctx):
    yield from ctx.observe()
    return Result('completed','fresh_after_startup_jitter')
   try:
    result=scheduler.run('ready',observe,2,False,False)
    self.assertEqual(result.reason,'fresh_after_startup_jitter')
    self.assertEqual(box.commands,[])
   finally:scheduler.close();e.close()
