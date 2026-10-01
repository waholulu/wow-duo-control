from test_support import requires_archive
import unittest
from pathlib import Path
import cv2
from control_policy import Policy
from vision_state import Vision,Observation

class NearZeroTargetTests(unittest.TestCase):
 @requires_archive('runs/paladin-judgement-first-01/evidence/*/00000106.jpg', 'runs/paladin-judgement-first-01/evidence/*/00000109.jpg', 'runs/paladin-judgement-first-01/evidence/*/00000110.jpg', 'runs/paladin-judgement-first-01/evidence/*/00000112.jpg', 'runs/paladin-judgement-first-01/evidence/*/00000115.jpg', 'runs/paladin-judgement-first-01/evidence/*/00000116.jpg', 'runs/paladin-judgement-first-01/evidence/*/00000120.jpg')
 def test_recorded_sliver_keeps_target_and_name_identity(self):
  v=Vision('classes/paladin/vision/profile-before-relocation.json')
  base=Path('runs/paladin-judgement-first-01/evidence')
  for n in (106,109,110,112,115,116,120):
   im=cv2.imread(str(next(base.glob(f'*/{n:08d}.jpg'))));o=v.observe(im)
   self.assertTrue(o.target,n);self.assertTrue(o.target_allowed,n)
   if n>=110:self.assertLessEqual(o.target_hp,.02)
 @requires_archive('runs/paladin-judgement-first-01/evidence/*/00000077.jpg', 'runs/paladin-judgement-first-01/evidence/*/00000427.jpg')
 def test_empty_target_still_empty(self):
  v=Vision('classes/paladin/vision/profile-before-relocation.json');base=Path('runs/paladin-judgement-first-01/evidence')
  for n in (77,427):
   o=v.observe(cv2.imread(str(next(base.glob(f'*/{n:08d}.jpg')))))
   self.assertFalse(o.target,n)
 def test_existing_melee_gets_bounded_finish_without_cancel_or_tab(self):
  p=Policy(attack_once=True,interact_key=65);p.attack_started=p.ctm_active=p.damaged_target=True;p.last_hp=.53;p.last_progress=0
  o=Observation(valid=True,player_hp=1,player_mana=1,target=True,target_allowed=True,target_hp=0,in_combat=True)
  for t in (0,.7,1.5,3,5.9):
   self.assertIsNone(p.step(o,t,0));self.assertIsNone(p.stopped);self.assertTrue(p.ctm_active)
  self.assertIsNone(p.step(o,6.1,0));self.assertEqual(p.stopped,'near_zero_target_unresolved')
 def test_never_new_interaction_with_empty_bar(self):
  p=Policy(attack_once=True,interact_key=65)
  o=Observation(valid=True,player_hp=1,player_mana=1,target=True,target_allowed=True,target_hp=0)
  self.assertIsNone(p.step(o,0,0));self.assertFalse(p.attack_started)
if __name__=='__main__':unittest.main()
