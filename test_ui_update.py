from test_support import requires_archive
import unittest,tempfile,json
from pathlib import Path
import cv2
from buff_timer import remaining,save
from combat_log_cv import evidence,CombatLogReader
from unittest.mock import patch
from vision_state import Vision
class UIUpdateTests(unittest.TestCase):
 @requires_archive('runs/ui-relocation.png', 'runs/ui-relocation-target.png')
 def test_relocated_player_and_unchanged_target(self):
  v=Vision('classes/paladin/vision/profile.json')
  for file,target in [('ui-relocation.png',False),('ui-relocation-target.png',True)]:
   import time
   im=cv2.imread('runs/'+file);o=v.observe(im)
   for _ in range(20):
    if o.valid and not o.player_mana_lower_bound:break
    time.sleep(.05);o=v.observe(im)
   self.assertTrue(o.valid);self.assertEqual(o.player_hp,1);self.assertEqual(o.player_mana,1);self.assertEqual(o.target,target)
 def test_timer_restart_expiry_clock_and_death(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'timer.json';g=Path(d)/'gate.json';save(p,34,100)
   self.assertEqual(remaining(p,34,3450,now=200),3350)
   self.assertEqual(remaining(p,34,3450,now=3600),0)
   self.assertEqual(remaining(p,34,3450,now=90),0)
   self.assertEqual(remaining(p,35,3450,now=200),0)
   g.write_text(json.dumps(dict(status='resolved',detected_at=150)))
   self.assertEqual(remaining(p,34,3450,g,now=200),0)
 def test_log_baseline_and_duplicate_do_not_create_kill_credit(self):
  rows=[];r=CombatLogReader([0,0,1,1],lambda kind,**kw:rows.append(kw))
  try:
   with patch('combat_log_cv.read_lines',return_value=['你杀死了甲。']):r.process(None,1);r.process(None,2)
   with patch('combat_log_cv.read_lines',return_value=['你杀死了乙。','你死了。']):r.process(None,3)
   self.assertEqual(rows[0]['new_evidence'],[]);self.assertEqual(rows[1]['new_evidence'],[])
   self.assertEqual([e['kind'] for e in rows[2]['new_evidence']],['kill_text','death_text'])
   self.assertTrue(all(x['auxiliary_only'] for x in rows))
  finally:r.close()
 @requires_archive('runs/paladin-cv-twenty-17/evidence/*/00000080.jpg', 'runs/paladin-cv-twenty-17/evidence/*/00000084.jpg')
 def test_combat_outline_changes_do_not_lose_target(self):
  v=Vision('classes/paladin/vision/profile.json')
  for name,spec in v.profile['templates'].items():
   if name.startswith('target_'):spec['threshold']=1.1
  for n in (80,84):
   p=next(Path('runs/paladin-cv-twenty-17/evidence').glob(f'*/{n:08d}.jpg'));im=cv2.imread(str(p))
   self.assertTrue(v.observe(im).target)
   im[660:677,1289:1385]=0
   self.assertFalse(v.observe(im).target)
 @requires_archive('runs/ui-relocation.png')
 def test_color_lower_bound_never_invents_full_mana_or_accepts_empty_bar(self):
  v=Vision('classes/paladin/vision/profile.json');im=cv2.imread('runs/ui-relocation.png')
  with patch('local_status_text.PercentReader.read',return_value=None):
   o=v.observe(im);self.assertTrue(o.valid);self.assertTrue(o.player_mana_lower_bound);self.assertGreaterEqual(o.player_mana,.65);self.assertLess(o.player_mana,.75)
   im[239:243,1018:1058]=0
   o=v.observe(im);self.assertFalse(o.valid);self.assertEqual(o.reason,'status_text_unreadable')
