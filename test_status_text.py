from test_support import requires_archive
import unittest
from unittest.mock import patch
import cv2
from local_status_text import PercentReader
from vision_state import Vision,Observation
from control_policy import Policy

class StatusTextTests(unittest.TestCase):
 def test_strict_percent_parser(self):
  for s in ('35','1003','101%','55/100',''):
   self.assertIsNone(PercentReader.parse([dict(text=s,confidence=1)]))
  self.assertEqual(PercentReader.parse([dict(text='35%',confidence=1)]),.35)
  self.assertIsNone(PercentReader.parse([dict(text='35%',confidence=.2)]))
 @requires_archive('runs/hp-settings-final.png')
 def test_recorded_native_text_full_bars(self):
  v=Vision('classes/paladin/vision/profile-before-relocation.json');im=cv2.imread('runs/hp-settings-final.png')
  o=v.observe(im);self.assertTrue(o.valid);self.assertEqual((o.player_hp,o.player_mana,o.target_hp),(1,1,1))
  count=v.percent_readers['player_mana'].calls
  self.assertTrue(v.observe(im).valid);self.assertEqual(v.percent_readers['player_mana'].calls,count)
 @requires_archive('runs/hp-settings-final.png')
 def test_unreadable_is_unknown_not_death(self):
  v=Vision('classes/paladin/vision/profile-before-relocation.json')
  with patch('local_status_text.PercentReader.read',return_value=None):
   self.assertEqual(v.observe(cv2.imread('runs/hp-settings-final.png')).reason,'status_text_unreadable')
 @requires_archive('runs/hp-settings-final.png')
 def test_ocr_conflict_with_cv_is_unknown(self):
  v=Vision('classes/paladin/vision/profile-before-relocation.json')
  with patch('local_status_text.PercentReader.read',return_value=.1):
   self.assertEqual(v.observe(cv2.imread('runs/hp-settings-final.png')).reason,'status_text_unreadable')
 @requires_archive('runs/hp-settings-final.png')
 def test_numeric_overlay_does_not_hide_small_health_fill(self):
  v=Vision('classes/paladin/vision/profile-before-relocation.json');im=cv2.imread('runs/hp-settings-final.png')
  im[690:693,617:721]=0;im[690:693,617:618]=(0,180,0)
  self.assertGreater(v.bar(im,'player_hp'),0);self.assertLess(v.bar(im,'player_hp'),.02)
 def test_brief_loss_waits_without_any_input(self):
  p=Policy(attack_once=True,interact_key=65);p.attack_started=p.ctm_active=True
  o=Observation(valid=True,player_hp=1,player_mana=1,target=False,in_combat=True)
  for t in (0,.6,1.1):self.assertIsNone(p.step(o,t,0));self.assertIsNone(p.stopped)
  self.assertEqual(p.step(o,1.3,0).reason,'cancel_click_to_move')
  p.step(o,1.4,0);self.assertEqual(p.stopped,'target_lost_under_threat')
 def test_changed_crop_never_reuses_cached_value(self):
  import numpy as np,time
  r=PercentReader();r.previous=np.zeros((10,20,3),dtype=np.uint8);r.value=1;r.last_at=time.monotonic()
  self.assertIsNone(r.read(np.full((10,20,3),200,dtype=np.uint8)))

class TargetChangeTests(unittest.TestCase):
 def test_in_combat_replacement_stops_before_new_attack(self):
  from dataclasses import replace
  from types import SimpleNamespace
  from runtime_skills import combat
  from test_support import Driver
  driver=Driver();base=driver.frame()
  rows=iter([('first',1),('first',.8),('replacement',1)])
  def frame():
   track,hp=next(rows,('replacement',1));driver.sequence+=1
   return replace(base,sequence=driver.sequence,captured_at=driver.now,target_track=track,
    observation=Observation(valid=True,player_hp=1,player_mana=1,target=True,target_allowed=True,target_hp=hp,in_combat=True))
  driver.frame=frame
  result=driver.run(combat(driver.ctx,SimpleNamespace(combat_options={},note_engagement=lambda s:None)))
  self.assertEqual(result.reason,'target_changed_under_threat')
