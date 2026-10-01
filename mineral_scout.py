"""Bounded forward survey for mineral candidates; no node clicks or pathfinding."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import time
import cv2
from mineral_monitor import mineral_candidates,PersistentCandidates
from vision_state import Vision
from vision_feed import Feed
from kmbox_tap import KMBox
from mineral_hover import ROOT
import re


from coordinate_reader import read_coordinate as coordinate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--steps',type=int,default=6)
    p.add_argument('--local-pulses',action='store_true',help='Finite local survey without coordinate navigation; at most three seconds of forward input')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if not a.execute or not 1<=a.steps<=6:p.error('Requires --execute and 1..6 finite steps')
    a.output.mkdir(parents=True,exist_ok=False)
    result=dict(state='RUNNING',steps=0,candidates=[],path=[],coordinate_navigation=not a.local_pulses)
    with ExitStack() as stack:
        f=Feed();stack.callback(f.close);b=KMBox();stack.callback(b.close)
        v=Vision(ROOT/'calibration/profile.json');tracker=PersistentCandidates()
        try:
            start=None
            with (a.output/'events.jsonl').open('x') as log:
                for step in range(a.steps+1):
                    for _ in range(3):
                        im,age=f.frame();obs=v.observe(im)
                        if age>.5 or not obs.valid or obs.in_combat or obs.player_hp<.95 or obs.target:
                            raise RuntimeError('Survey observation guard stopped movement')
                        candidates=tracker.update(mineral_candidates(im))
                        log.write(json.dumps(dict(step=step,time=time.time(),hp=obs.player_hp,candidates=candidates))+'\n');log.flush()
                        if candidates:
                            result.update(state='CANDIDATE_FOUND',candidates=candidates)
                            cv2.imwrite(str(a.output/'candidate.jpg'),im)
                            break
                        time.sleep(.1)
                    if result['state']=='CANDIDATE_FOUND':break
                    if not a.local_pulses:
                        pos=coordinate(im,a.output,step);result['path'].append(pos)
                        if start is None:start=pos
                        distance=((pos[0]-start[0])**2+(pos[1]-start[1])**2)**.5
                        if distance>1:raise RuntimeError('Coordinate displacement exceeded survey boundary')
                        if step>=2 and pos==result['path'][-3]:
                            result['state']='NO_COORDINATE_PROGRESS';break
                    if step==a.steps:
                        result['state']='STEP_LIMIT_NO_CANDIDATE';break
                    # OCR can be slow; capture again before issuing the finite pulse.
                    im,age=f.frame();obs=v.observe(im)
                    if age>.5 or not obs.valid or obs.in_combat or obs.player_hp<.95 or obs.target:
                        raise RuntimeError('Pre-input observation guard stopped movement')
                    b.tap(26,500);result['steps']+=1;time.sleep(.15)
        except Exception as exc:
            result.update(state='STOPPED',reason=str(exc))
        (a.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':main()
