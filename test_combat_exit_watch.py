import unittest
from combat_exit_watch import ExitWatch
from vision_state import Observation
from runtime_types import Snapshot
from types import SimpleNamespace
class CombatExitWatchTests(unittest.TestCase):
 def test_live_resolver_records_inputs_for_each_new_frame(self):
  from runtime_safety import resolve_threat
  now=[0.0];rows=[]
  obs=self.o(in_combat=False)
  source=SimpleNamespace(death_review_pending=False,
      combat_log=SimpleNamespace(latest=lambda *a:[]),
      peek=lambda:Snapshot(round(now[0]*10)+1,now[0]-.1,(1,0,0),obs,None,combat_known=True))
  stop=SimpleNamespace(wait=lambda seconds:now.__setitem__(0,now[0]+seconds))
  scheduler=SimpleNamespace(clock=lambda:now[0],source=source,stop_event=stop,
      _stopped=lambda:False,store=SimpleNamespace(emit=lambda kind,**kw:rows.append((kind,kw))))
  result=resolve_threat(scheduler,5)
  self.assertEqual(result.reason,'peace_confirmed')
  samples=[v for k,v in rows if k=='safety_exit_sample']
  self.assertGreaterEqual(len(samples),3)
  self.assertTrue(all(s['recent_log_kinds']==[] and not s['in_combat'] for s in samples))

 def test_repeated_frames_and_stale_gap_cannot_confirm_peace(self):
  w=ExitWatch(0);o=self.o(in_combat=False)
  for t in (0,2,4):self.assertEqual(w.step(o,True,.1,t,sequence=1),'observe')
  self.assertEqual(w.step(o,True,1,5,sequence=2),'observe')
  for t in (6,7,8):self.assertEqual(w.step(o,True,.1,t,sequence=t),'observe')
  self.assertEqual(w.step(o,True,.1,9,sequence=9),'peace_confirmed')
 def test_repolling_same_frame_after_it_ages_does_not_erase_fresh_quiet_evidence(self):
  w=ExitWatch(0);o=self.o(in_combat=False)
  self.assertEqual(w.step(o,True,.4,0,sequence=1),'observe')
  self.assertEqual(w.step(o,True,.6,.2,sequence=1),'observe')
  self.assertEqual(w.step(o,True,.4,1.5,sequence=2),'observe')
  self.assertEqual(w.step(o,True,.6,1.7,sequence=2),'observe')
  self.assertEqual(w.step(o,True,.4,3.1,sequence=3),'peace_confirmed')
 def o(self,**kw):
  d=dict(valid=True,player_hp=1,in_combat=True,target=False);d.update(kw);return Observation(**d)
 def test_kill_does_not_clear_another_attackers_threat(self):
  w=ExitWatch(0)
  self.assertEqual(w.step(self.o(xp_visible=True),True,.1,0),'observe')
  self.assertEqual(w.step(self.o(player_hp=.95),True,.1,1),'observe')
  self.assertEqual(w.step(self.o(player_hp=.90),True,.1,2),'escape')
 def test_full_health_recognition_loss_never_immediately_turns(self):
  w=ExitWatch(0)
  for t in (0,1,5,7):self.assertEqual(w.step(self.o(),True,.1,t),'observe')
  self.assertEqual(w.step(self.o(),True,.1,8),'escape')
 def test_requires_stable_peace_and_resets_on_further_damage(self):
  w=ExitWatch(0)
  for t in (0,1,2):self.assertEqual(w.step(self.o(in_combat=False),True,.1,t),'observe')
  self.assertEqual(w.step(self.o(in_combat=False),True,.1,3),'peace_confirmed')
  self.assertEqual(w.step(self.o(player_hp=.95,in_combat=False),True,.1,4),'observe')
 def test_stale_or_unknown_does_not_authorize_escape(self):
  w=ExitWatch(0)
  self.assertEqual(w.step(self.o(),True,1,20),'observe')
  self.assertEqual(w.step(self.o(valid=False),True,.1,20),'observe')
