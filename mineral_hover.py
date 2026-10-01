"""Confirm minimap candidates with local tooltip OCR; never click or navigate."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import time
import cv2
import numpy as np
from bag_probe import ROOT
from kmbox_tap import KMBox
from loot_probe import LootVision
from mineral_monitor import mineral_candidates,PersistentCandidates
from vision_feed import Feed
from vision_state import Vision

COPPER_NAMES={'铜矿','铜矿脉','劣质铜矿','coppervein'}


def copper_name(result):
    for item in result.get('items',[]):
        text=''.join(item.get('text','').lower().split())
        if text in COPPER_NAMES and item.get('confidence',0)>=.5:
            return text
    return None


def local_ocr(frame,path):
    # Right-side tooltip/map region; no chat or game-world text is consulted.
    crop=frame[190:880,1395:1640]
    cv2.imwrite(str(path),cv2.resize(crop,None,fx=3,fy=3))
    result=subprocess.run([str(ROOT/'ui_ocr')],input=str(path.resolve())+'\n',
                          capture_output=True,text=True,timeout=8,check=True)
    lines=result.stdout.splitlines()
    if len(lines)!=1:raise RuntimeError('Unexpected local OCR response')
    return json.loads(lines[0])


class Pointer:
    def __init__(self,feed,box):
        self.feed,self.box=feed,box
        self.world=Vision(ROOT/'calibration/profile.json');self.cursor=LootVision()
    def frame(self):
        self.captured=time.monotonic()
        im,age=self.feed.frame();o=self.world.observe(im)
        if age>.5 or not o.valid or o.in_combat or o.player_hp<.8:
            raise RuntimeError('Mineral hover requires fresh out-of-combat view')
        return im
    def move_to(self,x,y,bounds=(1465,215,1605,345)):
        left,top,right,bottom=bounds
        if not left<=x<=right or not top<=y<=bottom:
            raise ValueError('Destination outside calibrated minimap interior')
        previous=None
        for _ in range(20):
            im=self.frame()
            if previous is None:
                previous=self.cursor.cursor(im)
                if previous is None:raise RuntimeError('Unknown pointer; no further input')
                continue  # Full-area acquisition never directly authorizes input.
            ox=max(375,previous['x']-180);oy=max(150,previous['y']-180)
            roi=(ox,oy,min(360,1625-ox),min(360,885-oy))
            cursor=self.cursor.cursor(im,roi=roi)
            if cursor is None:raise RuntimeError('Unknown pointer; no further input')
            previous=cursor
            dx,dy=x-cursor['x'],y-cursor['y']
            if abs(dx)<=3 and abs(dy)<=3:return
            if time.monotonic()-self.captured>.5:raise RuntimeError('CV decision expired')
            # Conservative movement gain avoids overshooting the game boundary.
            mx,my=int(np.clip(round(dx/3),-50,50)),int(np.clip(round(dy/3),-50,50))
            mx,my=self.feed.mouse_delta(mx,my)
            self.box.command(f'km.move({mx},{my})');time.sleep(.12)
        raise TimeoutError('Minimap hover convergence limit')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute-hover',action='store_true');p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    with ExitStack() as stack:
        f=Feed();stack.callback(f.close)
        # No serial connection unless the user explicitly enables hover movement.
        box=KMBox() if args.execute_hover else None
        if box:stack.callback(box.close)
        pointer=Pointer(f,box);tracker=PersistentCandidates();points=[]
        for _ in range(6):
            points=tracker.update(mineral_candidates(pointer.frame()))
            if points:break
            time.sleep(.2)
        results=[]
        for index,point in enumerate(points[:4]):
            row=dict(candidate=point,tooltip_confirmed=False)
            if box:
                pointer.move_to(point['x'],point['y']);time.sleep(.25)
                names=[];ocr=[]
                for sample in range(2):
                    result=local_ocr(pointer.frame(),args.output/f'tooltip-{index}-{sample}.png')
                    ocr.append(result);names.append(copper_name(result));time.sleep(.15)
                row.update(ocr=ocr,tooltip_confirmed=bool(names[0] and names[0]==names[1]),name=names[0])
            results.append(row)
        result=dict(candidates=results,mining_verified=False,clicks_sent=0,
                    state='NO_CANDIDATE_OBSERVED' if not points else 'TOOLTIP_CHECK_FINISHED')
        (args.output/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False));print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
