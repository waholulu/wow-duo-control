import unittest
from control_policy import Policy
from vision_state import Observation,Vision
from types import SimpleNamespace
from runtime_skills import maintain_self_buff
from test_support import Driver

class JudgementOpenerTests(unittest.TestCase):
 def p(self):return Policy(attack_once=True,attack_key=30,interact_key=65,opener_key=33)
 def o(self,**kw):
  d=dict(valid=True,player_hp=1,player_mana=1,target=True,target_allowed=True,target_hp=1,bearing=0,opener_in_range=True);d.update(kw);return Observation(**d)
 def test_approach_then_judge_then_autoattack(self):
  p=self.p();a=p.step(self.o(opener_in_range=False),0,0);self.assertEqual((a.key,a.milliseconds),(26,200))
  self.assertEqual(p.step(self.o(),.5,0).reason,'ranged_opener')
  self.assertIsNone(p.step(self.o(),1,0))
  self.assertEqual(p.step(self.o(target_hp=.8,player_mana=.95,in_combat=True),1.2,0).reason,'interact_target')
 def test_unconfirmed_judge_never_starts_autoattack(self):
  p=self.p();p.step(self.o(),0,0);self.assertIsNone(p.step(self.o(),3.1,0));self.assertEqual(p.stopped,'opener_not_confirmed')
 def test_cooldown_and_seal_timer_preserved(self):
  p=self.p();p.buff_cast_at=0;p.step(self.o(),0,0);p.step(self.o(target_hp=.8,player_mana=.95),.5,0)
  self.assertIsNone(p.step(self.o(target_hp=.7),9,0))
  self.assertEqual(p.step(self.o(target_hp=.6),10.3,0).reason,'cooldown_strike');self.assertEqual(p.buff_cast_at,0)
 def test_unknown_range_stops(self):
  p=self.p();self.assertIsNone(p.step(self.o(opener_in_range=None),0,0));self.assertEqual(p.stopped,'opener_range_unreadable')
 def test_low_mana_preserves_heal_reserve(self):
  p=self.p();self.assertIsNone(p.step(self.o(player_mana=.34,in_combat=True),0,0));self.assertEqual(p.stopped,'opener_mana_reserve')
 def test_range_cue_red_and_pale(self):
  import cv2
  v=Vision('classes/paladin/vision/profile-before-relocation.json')
  self.assertFalse(v.opener_range(cv2.imread('runs/judgement-resume-before.png'),True))
  self.assertTrue(v.opener_range(cv2.imread('runs/judgement-tooltip-verified.png'),True))
 def test_blessing_self_target_and_timer(self):
  d=Driver();d.observation.target=True;s=SimpleNamespace(self_buff_options=dict(verified=True,key=34,self_key=58,refresh_seconds=3450))
  r=d.run(maintain_self_buff(d.ctx,s));self.assertEqual(r.status,'completed');self.assertEqual([a.values[0] for a in d.actions],[58,34,41])
  d.actions=[];d.run(maintain_self_buff(d.ctx,s));self.assertEqual(d.actions,[])
if __name__=='__main__':unittest.main()
