"""Native chat tab selection confirmation from the selected gold border."""
import cv2
import numpy as np
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def selected(frame,spec):
 if spec.get('active_contrast'):
  values=[]
  for x,y,w,h in spec['active_contrast']['rois']:
   patch=frame[y:y+h,x:x+w]
   if patch.shape[:2]!=(h,w):return False
   values.append(float(np.percentile(cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY),90)))
  if values[0]<values[1]*spec['active_contrast'].get('ratio',1.3):return False
 if spec.get('anchors'):
  return all(selected(frame,anchor) for anchor in spec['anchors'])
 x,y,w,h=spec['roi'];t=cv2.imread(str(ROOT/spec['file']));p=frame[y:y+h,x:x+w]
 if t is None or p.shape!=t.shape:return False
 raw=cv2.matchTemplate(p,t,cv2.TM_CCOEFF_NORMED)[0,0]
 if raw>=spec.get('threshold',.9):return True
 # Transparent chat backgrounds can change the pixels behind otherwise fixed
 # native labels. Text-edge structure is an independent fallback at the exact
 # calibrated position; callers still combine multiple anchors and contrast.
 edge_threshold=spec.get('edge_threshold')
 if edge_threshold is None:return False
 template_edges=cv2.Canny(cv2.cvtColor(t,cv2.COLOR_BGR2GRAY),40,120)
 patch_edges=cv2.Canny(cv2.cvtColor(p,cv2.COLOR_BGR2GRAY),40,120)
 return cv2.matchTemplate(patch_edges,template_edges,cv2.TM_CCOEFF_NORMED)[0,0]>=edge_threshold
