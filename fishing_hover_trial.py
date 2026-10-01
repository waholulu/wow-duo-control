import time,json,cv2,numpy as np
from pathlib import Path
from kmbox_tap import KMBox
from vision_feed import Feed
from interaction_vision import LootVision
out=Path('runs')/('fishing-hover-'+time.strftime('%Y%m%d-%H%M%S'));out.mkdir();Path('runs/current-fishing-hover.txt').write_text(str(out))
f=Feed();v=LootVision();tpl=cv2.imread('calibration/fishing_bobber_trial.png')
try:
 with KMBox() as b:
  before,_=f.raw_frame();cv2.imwrite(str(out/'before.jpg'),before);b.tap(37,80);time.sleep(1.5)
  im,_=f.raw_frame();scores=cv2.matchTemplate(im[325:490,800:1250],tpl,cv2.TM_CCOEFF_NORMED);_,score,_,(x,y)=cv2.minMaxLoc(scores);x+=812;y+=340
  print(dict(out=str(out.resolve()),score=score,target=[x,y]),flush=True)
  if score<.65:raise RuntimeError('Ambiguous float')
  for i in range(8):
   im,age=f.raw_frame();c=v.cursor(im,(180,32,1555,990));print(c,flush=True);cv2.imwrite(str(out/f'position-{i}.jpg'),im)
   if not c:break
   dx=x-c['x'];dy=y-c['y']
   if abs(dx)<5 and abs(dy)<5:break
   b.command(f'km.move({round(dx/1.7)},{round(dy/1.7)})');time.sleep(.16)
  for i in range(200):
   im,_=f.raw_frame();cv2.imwrite(str(out/f'wait-{i:03}.jpg'),im);cv2.imwrite(str(out/'latest.jpg'),im);time.sleep(.07)
finally:f.close()
