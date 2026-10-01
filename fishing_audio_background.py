"""Bounded read-only capture of other players after our fishing session stops."""
import argparse,json,time,cv2
from pathlib import Path
from fishing_config import load_config,ROOT
from fishing_candidates import propose
from fishing_signals import LocalBiteObserver
from fishing_audio import FishingAudio
from vision_feed import Feed

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=float,default=40);a=p.parse_args()
 if not 1<=a.seconds<=60:raise ValueError('Bounded observation only')
 a.output.mkdir(parents=True,exist_ok=False);c=load_config(ROOT/'calibration/fishing_crowded.json');c['world_roi']=[450,240,850,250]
 f=Feed();audio=FishingAudio(a.output,json.loads((ROOT/'calibration/runtime.json').read_text())['audio']['device_uid']);log=(a.output/'events.jsonl').open('w');last={};observers=[];started=time.monotonic();next_scan=started
 templates=[cv2.resize(cv2.imread(str(ROOT/n)),None,fx=s,fy=s) for n in c['bobber_templates'] for s in c['template_scales']]
 try:
  i=0
  while time.monotonic()-started<a.seconds:
   im,age=f.raw_frame()
   if age>.5:continue
   now=time.monotonic()
   if now>=next_scan:
    candidates=propose(im,im,templates,c)
    for q in candidates:
     if all((q['x']-r['x'])**2+(q['y']-r['y'])**2>18**2 for r,_ in observers):observers.append((q,LocalBiteObserver(q['x'],q['y'],c)))
    next_scan=now+5
   for k,(q,observer) in enumerate(observers):
    m,patch=observer.update(im)
    if m and m['splash']>=150 and (m['drop']>=3 or m['ring_score']<.6) and now-last.get(k,-100)>2:
     last[k]=now;log.write(json.dumps(dict(event='other_splash_candidate',at=now,candidate=q,metrics=m,audio=audio.window(now),patch=f'event-{k}-{i}.png'))+'\n');log.flush()
     cv2.imwrite(str(a.output/f'event-{k}-{i}.png'),patch);cv2.imwrite(str(a.output/f'event-{k}-{i}-reference.png'),observer.reference)
   if i%10==0:cv2.imwrite(str(a.output/f'frame-{i:03}.jpg'),im)
   i+=1;time.sleep(.04)
 finally:
  log.close();f.close();audio.close()
 print(a.output,flush=True)
if __name__=='__main__':main()
