import unittest
from pathlib import Path
import cv2
from vision_state import Vision
class TargetFrameEdge(unittest.TestCase):
 def test_non_wolf_target_is_detected_and_calibrated(self):
  p=Path('tests/fixtures/vendor-cycle-hunt-02/no-target-exception.jpg')
  if not p.exists():self.fail('Live regression frame unavailable')
  v=Vision('calibration/profile.json');im=cv2.imread(str(p));o=v.observe(im)
  self.assertFalse(v.matches(im,'target_anchor'))
  self.assertTrue(o.target);self.assertTrue(o.target_allowed)
 def test_recorded_empty_target_frames_remain_empty(self):
  v=Vision('calibration/profile.json');count=0
  for p in Path('tests/fixtures/hunt-skin-12/cycle-1/kill').glob('*.jpg'):
   im=cv2.imread(str(p))
   if not v.matches(im,'target_anchor'):
    self.assertFalse(v.observe(im).target);count+=1
  if not count:self.fail('Recorded negative frames unavailable')
if __name__=='__main__':unittest.main()
