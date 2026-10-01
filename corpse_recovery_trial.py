"""Supervised shared corpse recovery calibration, raw-window guarded inputs."""
import argparse,json,time,subprocess,math,re
from pathlib import Path
import cv2
import numpy as np
from navigation_coordinate import coordinate,parse
from collections import Counter
from navigate_local import steering
from vision_feed import Feed
from interaction_vision import LootVision
from kmbox_tap import KMBox
from control_lock import controller_lock
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'runs/corpse-recovery';OUT.mkdir(exist_ok=True)
def parse_ghost_coordinate(row):
 value=parse(row)
 if value is not None:return value
 items=row.get('items',[])
 if not items or any(i.get('confidence',0)<.9 for i in items):return None
 text=' '.join(i['text'] for i in items).strip()
 # A world nameplate line can intersect the one-decimal coordinate dots.
 match=re.fullmatch(r'(\d{2}[.,:\-]\d)[\s(\-]+(\d{2}[.,:\-]\d)[)\s]*',text)
 if not match:return None
 return tuple(float(re.sub(r'[,:\-]','.',v)) for v in match.groups())
class Recovery:
 def __init__(self):
  self.feed=Feed();self.v=LootVision();self.n=0
  self.title=cv2.imread(str(ROOT/'runs/resurrection-current.png'))[3:26,184:300]
 def frame(self):
  im,age=self.feed.raw_frame()
  score=cv2.matchTemplate(im[3:26,184:300],self.title,cv2.TM_CCOEFF_NORMED)[0,0]
  if age>.5 or score<.9:raise RuntimeError(f'Raw window guard failed {age} {score}')
  return im
 def ocr(self,im,rect,name):
  x,y,w,h=rect;path=OUT/f'{name}.png';cv2.imwrite(str(path),cv2.resize(im[y:y+h,x:x+w],None,fx=3,fy=3))
  r=subprocess.run([str(ROOT/'ui_ocr')],input=str(path)+'\n',text=True,capture_output=True,check=True)
  return json.loads(r.stdout)
 def click(self,b,x,y):
  for i in range(60):
   im=self.frame();c=self.v.cursor(im,roi=(180,30,1560,990))
   if c is None:raise RuntimeError('Raw cursor unavailable')
   dx,dy=x-c['x'],y-c['y']
   if abs(dx)<4 and abs(dy)<4:b.command('km.click(0)');return
   b.command(f'km.move({max(-35,min(35,round(dx/3)))},{max(-35,min(35,round(dy/3)))})');time.sleep(.08)
  raise RuntimeError('Cursor convergence limit')
 def save(self,name):
  im=self.frame();cv2.imwrite(str(OUT/f'{name}.png'),im);return im
 def ghost(self):
  im=self.frame()
  sat=float(np.median(cv2.cvtColor(im[350:650,650:1300],cv2.COLOR_BGR2HSV)[:,:,1]))
  ghost_template=cv2.imread(str(ROOT/'runs/corpse-recovery/released.png'))[198:230,1421:1453]
  ghost_score=float(cv2.matchTemplate(im[198:230,1421:1453],ghost_template,cv2.TM_CCOEFF_NORMED)[0,0])
  if ghost_score<.9:raise RuntimeError(f'Ghost scene guard failed: saturation {sat}, icon {ghost_score}')
  return im
 def location_once(self):
  values=[]
  for _ in range(2):
   im=self.ghost();paths=[];self.n+=1
   for i,scale in enumerate([3,4,5,6]):
    path=OUT/f'rawcoord-{self.n}-{i}.png';cv2.imwrite(str(path),cv2.resize(im[307:334,1555:1665],None,fx=scale,fy=scale));paths.append(str(path))
   for i,scale in enumerate([3,4,5]):
    path=OUT/f'rawcoord-tight-{self.n}-{i}.png';cv2.imwrite(str(path),cv2.resize(im[312:332,1558:1648],None,fx=scale,fy=scale));paths.append(str(path))
   output=subprocess.run([str(ROOT/'coordinate_ocr')],input='\n'.join(paths)+'\n',text=True,capture_output=True,timeout=6,check=True)
   rows=[json.loads(line) for line in output.stdout.splitlines()]
   votes=Counter(p for row in rows if (p:=parse_ghost_coordinate(row)) is not None)
   if not votes or votes.most_common(1)[0][1]<3:
    m=np.float32([[1.2237871674491392,0,-272.3536776212833],[0,1.244360902255639,-150.6766917293233]])
    norm=cv2.warpAffine(im,m,(1920,1080),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP)
    value=coordinate(norm,OUT,self.n)
   else:value=votes.most_common(1)[0][0]
   if not (48<=value[0]<=54 and 34<=value[1]<=45):raise RuntimeError('Coordinate outside calibrated corpse route')
   values.append(value)
  if values[0]!=values[1]:raise RuntimeError('Coordinates unstable')
  return values[0]
 def location(self):
  for attempt in range(3):
   try:return self.location_once()
   except (ValueError,RuntimeError):
    if attempt==2:raise
    time.sleep(.15)
 def navigate(self,b,target):
  previous=self.location();heading=None;stuck=0;best=math.dist(previous,target);unproductive=0;started=time.monotonic()
  def pulse(key,ms):
   self.ghost();b.tap(key,ms);time.sleep(ms/1000+.12)
  for step in range(100):
   if time.monotonic()-started>240:raise RuntimeError('Trial duration limit')
   im=self.save('latest');data=self.ocr(im,(780,170,370,155),'navigation-dialog')
   texts=[x['text'] for x in data['items']]
   row=dict(at=time.time(),target=target,step=step,position=previous,distance=math.dist(previous,target),dialog=texts)
   with (OUT/'navigation.jsonl').open('a') as log:log.write(json.dumps(row,ensure_ascii=False)+'\n')
   print(json.dumps(row,ensure_ascii=False),flush=True)
   if any('复活' in t for t in texts):
    self.save('resurrection-dialog');return dict(state='RESURRECTION_DIALOG',**row)
   if math.dist(previous,target)<.25:return dict(state='AT_WAYPOINT',**row)
   if heading is not None:
    error=steering(previous,heading,target)
    if abs(error)>.3:pulse(79 if error>0 else 80,min(450,max(40,round(abs(error)/math.pi*1000))))
   for _ in range(2):pulse(26,500)
   current=self.location();distance=math.dist(current,previous)
   if distance>1.2:raise RuntimeError('Implausible coordinate jump')
   stuck=stuck+1 if distance<.07 else 0
   if stuck>=2:raise RuntimeError('Movement blocked')
   if distance>=.07:heading=math.atan2(current[1]-previous[1],current[0]-previous[0])
   remaining=math.dist(current,target)
   if remaining<best-.05:best=remaining;unproductive=0
   else:unproductive+=1
   if unproductive>=10:raise RuntimeError('Route not progressing')
   previous=current
  raise RuntimeError('Step limit')
 def close(self):self.feed.close()
def main():
 global OUT
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=OUT);p.add_argument('--release',action='store_true');p.add_argument('--resurrect',action='store_true');p.add_argument('--navigate',action='store_true');p.add_argument('--turn-around',action='store_true');p.add_argument('--target',nargs=2,type=float,default=[51.1,35.2]);a=p.parse_args()
 OUT=a.output.resolve();OUT.mkdir(parents=True,exist_ok=True)
 with controller_lock():
  r=Recovery()
  try:
   im=r.save('before');data=r.ocr(im,(795,180,330,74),'dialog');print(json.dumps(data,ensure_ascii=False),flush=True)
   if a.release:
    assert any('释放灵魂' in i['text'] for i in data['items'])
    with KMBox() as b:r.click(b,889,230)
    time.sleep(2);im=r.save('released');print(json.dumps(r.ocr(im,(178,30,1564,994),'released-ocr'),ensure_ascii=False),flush=True)
   if a.resurrect:
    verified=[]
    for attempt in range(8):
     im=r.ghost();data=r.ocr(im,(795,180,330,74),f'accept-dialog-{attempt}')
     accept=[i for i in data['items'] if i['text']=='接受' and i['confidence']>=.8]
     if any('现在复活' in i['text'] and i['confidence']>=.8 for i in data['items']) and len(accept)==1:
      verified.append(accept[0])
      if len(verified)>=2:break
     time.sleep(.15)
    if len(verified)<2:raise RuntimeError('Resurrection dialog not confirmed twice')
    accept=[verified[-1]]
    x,y,w,h=accept[0]['box'];cx=round(795+(x+w/2)*330);cy=round(180+(1-y-h/2)*74)
    with KMBox() as b:r.click(b,cx,cy)
    time.sleep(2);r.save('after-resurrection')
    print(json.dumps(dict(action='accept_resurrection',click=[cx,cy])))
   if a.navigate:
    with KMBox() as b:
     if a.turn_around:
      for _ in range(2):r.ghost();b.tap(79,500);time.sleep(.65)
     result=r.navigate(b,tuple(a.target));(OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result)
  except Exception as exc:
   result=dict(state='STOPPED',reason=str(exc),at=time.time(),target=a.target)
   (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
   print(json.dumps(result,ensure_ascii=False),flush=True)
   raise
  finally:r.close()
if __name__=='__main__':main()
