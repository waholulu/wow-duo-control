"""Read-only CV monitor for persistent mineral-like minimap dots.
Dots are candidates, never proof of a mine and never authorize navigation/clicks.
"""
import argparse
import json
from pathlib import Path
import time
import cv2
import numpy as np
from vision_feed import Feed
from vision_state import Vision

ROOT=Path(__file__).resolve().parent


def mineral_candidates(frame, inner_radius=12, outer_radius=64):
    if frame.shape[:2]!=(1080,1920):
        return []
    x0,y0=1460,205
    patch=frame[y0:355,x0:1615]
    hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
    mask=cv2.inRange(hsv,np.array([15,130,180]),np.array([40,255,255]))
    _,_,stats,centers=cv2.connectedComponentsWithStats(mask)
    found=[]
    for s,c in zip(stats[1:],centers[1:]):
        x,y=c+[x0,y0]
        distance=((x-1535)**2+(y-280)**2)**.5
        if 2<=s[4]<=55 and s[2]<=12 and s[3]<=12 and inner_radius<distance<outer_radius:
            found.append(dict(x=round(float(x),1),y=round(float(y),1),area=int(s[4])))
    return found


class PersistentCandidates:
    def __init__(self):self.tracks=[]
    def update(self,points):
        previous=self.tracks;self.tracks=[]
        for p in points:
            matches=[v for v in previous if abs(v['x']-p['x'])<=4 and abs(v['y']-p['y'])<=4]
            count=max((v['frames'] for v in matches),default=0)+1
            self.tracks.append(dict(p,frames=count))
        return [v for v in self.tracks if v['frames']>=3]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds',type=float,default=15)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not 0<args.seconds<=60:p.error('--seconds must be in (0,60]')
    args.output.mkdir(parents=True,exist_ok=False)
    f=Feed();v=Vision(ROOT/'calibration'/'profile.json');tracker=PersistentCandidates()
    deadline=time.monotonic()+args.seconds;ever=[];samples=0
    try:
        with (args.output/'samples.jsonl').open('x') as log:
            while time.monotonic()<deadline:
                im,age=f.frame();obs=v.observe(im)
                if age>.5 or not obs.valid:raise RuntimeError('Unusable game frame')
                points=mineral_candidates(im);stable=tracker.update(points);samples+=1
                row=dict(time=time.time(),candidates=points,persistent=stable)
                log.write(json.dumps(row)+'\n');log.flush()
                if stable:
                    ever.extend(stable)
                    cv2.imwrite(str(args.output/f'candidate-{samples}.jpg'),im)
                time.sleep(.5)
        result=dict(samples=samples,persistent_candidates=len(ever),
                    state='CANDIDATES_REQUIRE_TOOLTIP_CONFIRMATION' if ever else 'NO_CANDIDATE_OBSERVED',
                    mining_verified=False,input_sent=False)
        (args.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
    finally:f.close()

if __name__=='__main__':main()
