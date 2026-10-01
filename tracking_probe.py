"""Inspect the calibrated tracking buff by local CV hover and local OCR."""
import argparse
from contextlib import ExitStack
import json
import subprocess
import time
from pathlib import Path
import cv2
from mineral_hover import Pointer,ROOT
from vision_feed import Feed
from kmbox_tap import KMBox


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not args.execute:p.error('--execute required for pointer hover')
    args.output.mkdir(parents=True,exist_ok=False)
    with ExitStack() as stack:
        feed=Feed();stack.callback(feed.close)
        box=KMBox();stack.callback(box.close);pointer=Pointer(feed,box)
        template=cv2.imread(str(ROOT/'calibration/mineral_tracking_icon.png'))
        im=pointer.frame()
        score=float(cv2.matchTemplate(im[152:181,1385:1414],template,cv2.TM_CCOEFF_NORMED).max())
        if score<.9:raise RuntimeError('Tracking icon changed; no hover')
        pointer.move_to(1398,166,bounds=(1385,154,1414,181));time.sleep(.4)
        reads=[]
        for sample in range(2):
            im=pointer.frame();path=args.output/f'tooltip-{sample}.png'
            cv2.imwrite(str(path),cv2.resize(im[145:880,1180:1640],None,fx=2,fy=2))
            result=subprocess.run([str(ROOT/'ui_ocr')],input=str(path.resolve())+'\n',
                                  text=True,capture_output=True,timeout=8,check=True)
            reads.append(json.loads(result.stdout))
        confirmed=all(any('寻找矿物' in r.get('text','') for r in result.get('items',[])) for result in reads)
        outcome=dict(tracking_confirmed=confirmed,icon_score=score,ocr=reads,clicks_sent=0)
        (args.output/'result.json').write_text(json.dumps(outcome,ensure_ascii=False,indent=2))
        pointer.move_to(800,250,bounds=(420,200,1380,840))
        print(json.dumps(outcome,ensure_ascii=False))

if __name__=='__main__':main()
