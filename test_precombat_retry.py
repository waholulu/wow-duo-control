from test_support import requires_archive
import unittest
from runtime_skills import verify_unengaged,roam
from runtime_types import GuardFailed
from test_support import Driver
class PrecombatRetryTests(unittest.TestCase):
 def test_roam_waits_for_delayed_target_clear(self):
  class DelayedClear(Driver):
   def frame(self):
    self.observation.target=not self.actions or self.now<1.2
    self.observation.target_allowed=self.observation.target
    self.observation.target_hp=1 if self.observation.target else 0
    return super().frame()
  d=DelayedClear()
  result=d.run(roam(d.ctx))
  self.assertEqual((result.status,result.reason),('completed','STEP_COMPLETE'))
  self.assertEqual([a.reason for a in d.actions],
                   ['clear_target','patrol_turn','patrol_move','patrol_move'])

 def test_healthy_peaceful_full_target_can_resume_search(self):
  d=Driver();d.observation.target=True;d.observation.target_hp=1
  self.assertEqual(d.run(verify_unengaged(d.ctx)).status,'completed');self.assertEqual(d.actions,[])
 def test_damaged_target_never_authorizes_new_search(self):
  d=Driver();d.observation.target=True;d.observation.target_hp=.2
  self.assertEqual(d.run(verify_unengaged(d.ctx)).reason,'target_already_damaged');self.assertEqual(d.actions,[])
 def test_combat_and_low_health_block(self):
  for hp,combat in ((1,True),(.8,False)):
   d=Driver();d.observation.player_hp=hp;d.observation.in_combat=combat
   with self.assertRaises(GuardFailed):d.run(verify_unengaged(d.ctx))
   self.assertEqual(d.actions,[])

class DimTargetRegressionTests(unittest.TestCase):
 @requires_archive('runs/paladin-cv-twenty-16/evidence/*/00000067.jpg', 'runs/paladin-cv-twenty-16/evidence/*/00000073.jpg', 'runs/paladin-cv-twenty-16/evidence/*/00000083.jpg', 'runs/paladin-cv-twenty-16/evidence/*/00000089.jpg')
 def test_actual_dim_frame_remains_target_until_death(self):
  from pathlib import Path
  import cv2
  from vision_state import Vision
  v=Vision('classes/paladin/vision/profile-before-relocation.json')
  for n,expected in ((67,True),(73,True),(83,True),(89,False)):
   f=next(Path('runs/paladin-cv-twenty-16/evidence').glob(f'*/{n:08d}.jpg'))
   self.assertEqual(v.observe(cv2.imread(str(f))).target,expected)
