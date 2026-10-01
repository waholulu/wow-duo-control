import unittest
from pathlib import Path
import cv2,numpy as np
from vision_state import Vision
class HealthOcclusionTests(unittest.TestCase):
 def test_recorded_leading_damage_occlusion(self):
  p=Path('tests/fixtures/vendor-combat-recovery-01/000260.jpg')
  if not p.exists():self.fail('Recorded frame unavailable')
  v=Vision('calibration/profile.json');hp=v.bar(cv2.imread(str(p)),'player_hp')
  self.assertGreater(hp,.4);self.assertLess(hp,.5)
 def test_low_fill_and_isolated_right_pixels_stay_low(self):
  v=Vision('calibration/profile.json');roi=v.profile['bars']['player_hp']['roi'];x,y,w,h=roi
  frame=np.zeros((1080,1920,3),np.uint8);frame[y:y+h,x:x+20]=(0,180,0)
  self.assertLess(v.bar(frame,'player_hp'),.4)
  frame[y:y+h,x:x+w]=0;frame[y:y+h,x+40:x+w]=(0,180,0)
  self.assertEqual(v.bar(frame,'player_hp'),0)
if __name__=='__main__':unittest.main()
