import unittest,json
from pathlib import Path
import cv2
import numpy as np
from fishing_candidates import propose
from fishing_trial import select_bobber,match,bite_trigger
from fishing_config import load_config,ROOT
from interaction_vision import BagVision

class CandidateTests(unittest.TestCase):
 def test_existing_high_score_does_not_hide_new_candidate(self):
  c=load_config(ROOT/'calibration/fishing_crowded.json');p=ROOT/'runs/fishing-hover-20260929-110342'
  templates=[cv2.resize(cv2.imread(str(ROOT/n)),None,fx=s,fy=s) for n in c['bobber_templates'] for s in c['template_scales']]
  result=propose(cv2.imread(str(p/'before.jpg')),cv2.imread(str(p/'cast.jpg')),templates,c)
  self.assertTrue(any(abs(r['x']-908)<12 and abs(r['y']-393)<12 for r in result))
  self.assertTrue(any(abs(r['x']-1073)<12 for r in result))
 def test_ambiguous_new_candidates_rejected(self):
  c=load_config(ROOT/'calibration/fishing_crowded.json')
  with self.assertRaisesRegex(RuntimeError,'ambiguous'):
   select_bobber([dict(score=.9,old=.1,x=900,y=350),dict(score=.8,old=.2,x=1050,y=360)],c)
 def test_recorded_hover_distinction(self):
  p=ROOT/'runs/fishing-hover-20260929-110342';c=load_config(ROOT/'calibration/fishing_crowded.json');gear=cv2.imread(str(ROOT/c['gear_template']))
  other,_=match(cv2.imread(str(p/'hover-0.jpg')),gear,[1028,371,110,110])
  own,_=match(cv2.imread(str(p/'hover-1.jpg')),gear,[863,348,110,110])
  self.assertLess(other,.9);self.assertGreater(own,.95)
 def test_new_bag_layout(self):
  p=json.loads((ROOT/'calibration/fishing_crowded_bag_layout.json').read_text());r=BagVision(p).observe(cv2.imread(str(ROOT/'runs/fishing-crowded-preflight/recalibrated-bag-failed.jpg')))
  self.assertTrue(r['valid']);self.assertEqual(r['capacity'],42);self.assertEqual(r['empty'],24)
 def test_own_bite_but_not_nearby_splash(self):
  c=load_config(ROOT/'calibration/fishing_crowded.json');p=ROOT/'runs/fishing-duck-clear-20260929-110814'
  frames=[cv2.imread(str(f)) for f in sorted(p.glob('patch-*.png'))]
  x,y,w,h=c['ring_roi'];tpl=frames[5][y:y+h,x:x+w];fired=[];previous=[]
  from fishing_config import crop
  for i in range(10,100):
   im=frames[i];base=np.median(frames[i-5:i],axis=0);delta=im.astype(float)-base
   splash=int((crop(delta.mean(2),c['splash_roi'])>c['bright_delta']).sum())
   submerged=int((crop(delta.mean(2),c['submerged_roi']) < -c['dark_delta']).sum())
   _,score,_,(sx,sy)=cv2.minMaxLoc(cv2.matchTemplate(crop(im,c['ring_search_roi']),tpl,cv2.TM_CCOEFF_NORMED))
   if abs(sx+c['ring_search_roi'][0]-x)>c['ring_horizontal_tolerance']:score=0
   prior=float(np.median(previous[-3:])) if previous else None
   if bite_trigger(c,sy+c['ring_search_roi'][1]-y,score,submerged,splash,prior):fired.append(i)
   previous.append(score)
  self.assertTrue(any(13<=i<=19 for i in fired),fired)
  self.assertFalse(any(i<13 or i>22 for i in fired),fired)
 def test_recorded_missed_duck_bite_and_quiet_frames(self):
  c=load_config(ROOT/'calibration/fishing_crowded.json')
  for cast,low,high in [('cast-04',15.7,16.5),('cast-05',9.7,10.4),('cast-06',15.9,16.5)]:
   p=ROOT/'runs/fishing-crowded-live-07'/cast/'events.jsonl'
   events=[json.loads(l) for l in p.read_text().splitlines()]
   scores=[];fired=[]
   for e in events:
    if e['event']!='watch':continue
    prior=float(np.median(scores[-3:])) if scores else None
    if bite_trigger(c,e['drop'],e['ring_score'],e['submerged'],e['splash'],prior):fired.append(e['t'])
    scores.append(e['ring_score'])
   self.assertTrue(fired)
   self.assertTrue(all(low<t<high for t in fired),fired)
 def test_no_fish_false_trigger_rejected(self):
  c=load_config(ROOT/'calibration/fishing_crowded.json')
  p=ROOT/'runs/fishing-enabled-20260929-113400/cast-25/events.jsonl'
  scores=[];fired=[]
  for line in p.read_text().splitlines():
   e=json.loads(line)
   if e['event']!='watch':continue
   prior=float(np.median(scores[-3:])) if scores else None
   if e['t']>c['minimum_bite_seconds'] and bite_trigger(c,e['drop'],e['ring_score'],e['submerged'],e['splash'],prior):fired.append(e['t'])
   scores.append(e['ring_score'])
  self.assertFalse(fired)
if __name__=='__main__':unittest.main()
