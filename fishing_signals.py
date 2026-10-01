"""Passive local bite metrics, shared for labeled neighboring-bobber observations."""
import cv2
import numpy as np
from fishing_config import crop

class LocalBiteObserver:
    def __init__(self,x,y,config):
        self.x=x;self.y=y;self.c=config;self.frames=[];self.reference=None
    def update(self,frame):
        c=self.c;px,py,w,h=c['patch_offset'];patch=crop(frame,[self.x+px,self.y+py,w,h])
        if patch.shape[:2]!=(h,w):return None,None
        self.frames.append(patch.astype(float))
        if self.reference is None:
            if len(self.frames)==c['reference_frame']:self.reference=crop(patch,c['ring_roi']).copy()
            return None,patch
        if len(self.frames)<=c['baseline_frames']:return None,patch
        previous=self.frames[:-1][-c['median_frames']:];base=np.median(previous,axis=0);self.frames=self.frames[-c['baseline_frames']:]
        delta=patch.astype(float)-base
        _,score,_,(x,y)=cv2.minMaxLoc(cv2.matchTemplate(crop(patch,c['ring_search_roi']),self.reference,cv2.TM_CCOEFF_NORMED))
        ox,oy=c['ring_roi'][:2]
        if abs(x+c['ring_search_roi'][0]-ox)>c.get('ring_horizontal_tolerance',999):score=0.
        return dict(drop=y+c['ring_search_roi'][1]-oy,ring_score=float(score),splash=int((crop(delta.mean(2),c['splash_roi'])>c['bright_delta']).sum()),submerged=int((crop(delta.mean(2),c['submerged_roi']) < -c['dark_delta']).sum())),patch
