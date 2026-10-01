from test_support import requires_archive
import unittest,json
from pathlib import Path
import cv2
from local_status_text import PercentReader
class PercentCacheTests(unittest.TestCase):
 @requires_archive('runs/paladin-cv-twenty-17/events.jsonl', 'runs/paladin-cv-twenty-17/evidence/*/*.jpg', 'runs/paladin-cv-twenty-18/events.jsonl', 'runs/paladin-cv-twenty-18/evidence/*/*.jpg', 'runs/paladin-cv-twenty-21/events.jsonl', 'runs/paladin-cv-twenty-21/evidence/*/*.jpg')
 def test_recorded_different_percentages_do_not_match_cache(self):
  rows=[]
  for run in (17,18,21):
   root=Path(f'runs/paladin-cv-twenty-{run}');images={p.name:p for p in (root/'evidence').glob('*/*.jpg')}
   for line in (root/'events.jsonl').read_text().splitlines():
    e=json.loads(line)
    if e['kind']!='observation' or not e['observation']['valid']:continue
    p=images.get(f"{e['frame']:08d}.jpg")
    if p is not None:rows.append((e['observation']['player_mana'],cv2.imread(str(p))[233:248,979:1018]))
  matched=0
  for i,(value,a) in enumerate(rows):
   for other,b in rows[i+1:]:
    score=cv2.matchTemplate(a,b,cv2.TM_CCOEFF_NORMED)[0,0]
    if value!=other:self.assertLess(score,.975)
    elif score>=.975:matched+=1
  self.assertGreater(matched,100)
