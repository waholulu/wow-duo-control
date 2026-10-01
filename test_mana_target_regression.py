from test_support import requires_archive
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import cv2
from target_continuity import TargetContinuity
from vision_state import Vision,Observation
from runtime_types import Result
from test_support import make_controller

class ManaTargetRegressionTests(unittest.TestCase):
 @requires_archive('runs/paladin-cv-twenty-09/evidence/*/00000126.jpg', 'runs/paladin-cv-twenty-09/evidence/*/00000127.jpg', 'runs/paladin-cv-twenty-09/evidence/*/00000128.jpg', 'runs/paladin-cv-twenty-09/evidence/*/00000129.jpg', 'runs/paladin-cv-twenty-09/evidence/*/00000130.jpg', 'runs/paladin-cv-twenty-09/evidence/*/00000131.jpg', 'runs/paladin-cv-twenty-09/evidence/*/00000132.jpg', 'runs/paladin-cv-twenty-09/evidence/*/00000133.jpg')
 def test_actual_interruption_frames_keep_target_track(self):
  v=Vision('classes/paladin/vision/profile-before-relocation.json');tracker=TargetContinuity()
  base=Path('runs/paladin-cv-twenty-09/evidence')
  tracks=[]
  for n in (126,127,128,129,130,131,132,133):
   im=cv2.imread(str(next(base.glob(f'*/{n:08d}.jpg'))))
   # Reproduce resource OCR failure, not a disappearance of the target.
   with patch('local_status_text.PercentReader.read',return_value=None if n in (127,128,130,131,132) else .90):
    o=v.observe(im)
   self.assertTrue(o.target);self.assertTrue(o.target_allowed)
   tracks.append(tracker.update(o,v.target_label,(1,),1,n*.125))
  self.assertEqual(set(tracks),{'1'})
 def test_actual_change_still_creates_new_track(self):
  t=TargetContinuity();o=Observation(valid=True,target=True,target_allowed=True,target_hp=.3,in_combat=True)
  self.assertEqual(t.update(o,'name',(1,),1,0),'1')
  o.target_hp=1
  self.assertEqual(t.update(o,'name',(1,),1,.1),'2')
 def test_healthy_recognition_stop_does_not_escape(self):
  c=make_controller(['--no-loot','--kills','1'])
  c.s.source=SimpleNamespace(peek=lambda:SimpleNamespace(observation=Observation(valid=True,player_hp=1,in_combat=True)))
  calls=[]
  def run(name,*a,**kw):
   calls.append(name)
   if name=='ready':return Result('completed','ready')
   return Result('failed','target_changed_under_threat',{'xp_events':0})
  with patch.object(c,'run_skill',side_effect=run):result=c.run()
  self.assertTrue(result.facts['review_required']);self.assertNotIn('escape',calls)
