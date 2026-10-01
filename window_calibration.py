"""Three-landmark window registration; canonical pixels in, raw pixels out.

No title-bar clicks, OS settings, or input are needed to reacquire the window.
A changed layout that cannot be explained by one scale and translation is
rejected rather than reusing stale coordinates.
"""
import json
from pathlib import Path
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parent


class WindowCalibration:
    def __init__(self):
        self.config=json.loads((ROOT/'calibration/window.json').read_text())
        self.anchors=[(s,cv2.imread(str(ROOT/'calibration'/s['file']),0)) for s in self.config['anchors']]
        self.alternatives={s['name']:[cv2.imread(str(ROOT/'calibration'/p),0) for p in s.get('alternatives',[])] for s,_ in self.anchors}
        self.transform=(1.,0.,0.)
        self.generation=0
        self.last_scores=[]

    def validate(self,gray,transform):
        sx,sy,tx,ty=self.axes(transform);scores=[];centers=[]
        for spec,original in self.anchors:
            x,y,w,h=spec['rect'];template=cv2.resize(original,(round(w*sx),round(h*sy)))
            px,py=round(x*sx+tx),round(y*sy+ty)
            margin=3
            left,top=max(0,px-margin),max(0,py-margin)
            right,bottom=min(gray.shape[1],px+template.shape[1]+margin),min(gray.shape[0],py+template.shape[0]+margin)
            patch=gray[top:bottom,left:right]
            if patch.shape[0]<template.shape[0] or patch.shape[1]<template.shape[1]:return None
            _,score,_,loc=cv2.minMaxLoc(cv2.matchTemplate(patch,template,cv2.TM_CCOEFF_NORMED))
            for alternative in self.alternatives[spec['name']]:
                other=cv2.resize(alternative,(template.shape[1],template.shape[0]))
                _,value,_,point=cv2.minMaxLoc(cv2.matchTemplate(patch,other,cv2.TM_CCOEFF_NORMED))
                if value>score:score,loc=value,point
            if not np.isfinite(score) or score<.78:return None
            centers.append((left+loc[0]+template.shape[1]/2,top+loc[1]+template.shape[0]/2))
            scores.append(float(score))
        # Independent anchors must agree, rather than each drifting in its ROI.
        reference=np.array([(a['rect'][0]+a['rect'][2]/2,a['rect'][1]+a['rect'][3]/2) for a,_ in self.anchors])
        residual=np.array(centers)-(reference*[sx,sy]+[tx,ty])
        if np.max(np.linalg.norm(residual-residual.mean(axis=0),axis=1))>2.5:return None
        x,y,w,h=self.config['viewport']
        if x*sx+tx<0 or y*sy+ty<0 or (x+w)*sx+tx>gray.shape[1]+1 or (y+h)*sy+ty>gray.shape[0]+1:return None
        return scores

    def acquire(self,frame):
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        for layout in self.config.get('verified_layouts',[]):
            transform=tuple(layout['transform']);scores=self.validate(gray,transform)
            if scores:
                self.transform=transform;self.last_scores=scores;self.generation+=1;return True
        spec,original=self.anchors[0];x,y,w,h=spec['rect']
        small=cv2.resize(gray,None,fx=.5,fy=.5)
        candidates=[]
        for scale in np.arange(.5,2.01,.025):
            template=cv2.resize(original,(max(3,round(w*scale*.5)),max(3,round(h*scale*.5))))
            if template.shape[0]>small.shape[0] or template.shape[1]>small.shape[1]:continue
            _,score,_,loc=cv2.minMaxLoc(cv2.matchTemplate(small,template,cv2.TM_CCOEFF_NORMED))
            if score>.65:candidates.append((score,float(scale),loc))
        matches=[]
        for _,scale,loc in sorted(candidates,reverse=True)[:8]:
            for s in np.arange(max(.5,scale-.025),min(2.,scale+.025)+.001,.005):
                template=cv2.resize(original,(round(w*s),round(h*s)))
                left,top=max(0,loc[0]*2-8),max(0,loc[1]*2-8)
                patch=gray[top:min(gray.shape[0],top+template.shape[0]+16),left:min(gray.shape[1],left+template.shape[1]+16)]
                if patch.shape[0]<template.shape[0] or patch.shape[1]<template.shape[1]:continue
                _,score,_,point=cv2.minMaxLoc(cv2.matchTemplate(patch,template,cv2.TM_CCOEFF_NORMED))
                if score<.78:continue
                transform=(float(s),float(left+point[0]-x*s),float(top+point[1]-y*s))
                scores=self.validate(gray,transform)
                if scores:matches.append((sum(scores),transform,scores))
        if not matches:self.transform=None;return False
        matches.sort(reverse=True)
        best=matches[0]
        # Two geographically distinct valid windows are ambiguous.
        if any(abs(m[1][1]-best[1][1])+abs(m[1][2]-best[1][2])>25 for m in matches[1:]):
            self.transform=None;return False
        self.transform=best[1];self.last_scores=best[2];self.generation+=1
        return True

    def normalize(self,frame):
        if self.transform is None:return None
        scores=self.validate(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),self.transform)
        if scores is None:self.transform=None;return None
        self.last_scores=scores;sx,sy,tx,ty=self.axes(self.transform)
        matrix=np.array([[sx,0,tx],[0,sy,ty]],dtype=np.float32)
        output=cv2.warpAffine(frame,matrix,tuple(self.config['canonical_size']),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP)
        # Desktop chrome cannot become a target/cursor in canonical game space.
        x,y,w,h=self.config['viewport']
        output[:y]=0;output[y+h:]=0;output[:,:x]=0;output[:,x+w:]=0
        return output

    def mouse_delta(self,dx,dy):
        if self.transform is None:raise RuntimeError('No valid window transform for mouse input')
        sx,sy,_,_=self.axes(self.transform)
        return tuple(0 if n==0 else int(np.sign(n)*max(1,round(abs(n)*scale))) for n,scale in zip((dx,dy),(sx,sy)))

    @staticmethod
    def axes(transform):
        if len(transform)==3:
            s,x,y=transform;return s,s,x,y
        return transform

    def status(self):
        return dict(transform=self.transform,generation=self.generation,anchor_scores=self.last_scores)
