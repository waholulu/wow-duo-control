"""Supervised CTM trial: Tab, seal, F8; observe and always cancel movement."""
import argparse,cv2,time,json
from pathlib import Path
from dataclasses import asdict
from vision_feed import Feed
from vision_state import Vision
from kmbox_tap import KMBox
from control_lock import controller_lock
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(exist_ok=False)
with controller_lock():
 f=Feed();v=Vision('classes/paladin/vision/profile.json');rows=[]
 try:
  with KMBox() as b:
   try:
    im,age=f.frame();o=v.observe(im);assert o.valid and o.player_hp>.85 and not o.in_combat
    b.tap(43,80);time.sleep(.3);im,age=f.frame();o=v.observe(im);cv2.imwrite(str(a.output/'selected.jpg'),im)
    assert o.valid and o.target_allowed and o.player_hp>.85,asdict(o)
    b.tap(31,80);time.sleep(1.7)
    im,age=f.frame();o=v.observe(im);assert o.valid and o.target_allowed and o.player_hp>.85
    b.tap(65,80);started=time.monotonic()
    for i in range(40):
     im,age=f.frame();o=v.observe(im);rows.append(dict(at=time.monotonic()-started,**asdict(o)));cv2.imwrite(str(a.output/f'{i:02d}.jpg'),im)
     if not o.valid or o.player_hp<.65:break
     time.sleep(.4)
   finally:
    b.tap(22,20) # S cancels click-to-move, even on a failed observation.
 finally:
  f.close();(a.output/'observations.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows[-1] if rows else {}))
