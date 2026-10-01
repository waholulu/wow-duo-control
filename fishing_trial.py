"""Bounded live fishing trial for the inspected lake view; raw capture coordinates.
Every cast is associated with a newly appearing local bobber; sound alone never reels.
STOP in output directory cancels before subsequent input. Evidence is saved per cast.
"""
import argparse,json,time,re,subprocess,random
from fishing_config import load_config, crop, parse_options
from pathlib import Path
import cv2
import numpy as np
from kmbox_tap import KMBox
from vision_feed import Feed
from interaction_vision import LootVision
from fishing_inventory import FishingInventory, inventory_changed, full_notice
from fishing_candidates import propose, hover_candidate

ROOT=Path(__file__).resolve().parent

def random_delay(bounds,out,deadline):
 milliseconds=random.uniform(*bounds)
 end=time.monotonic()+milliseconds/1000
 while True:
  now=time.monotonic()
  if now>deadline or (out/'STOP').exists():raise RuntimeError('Cancelled during delay')
  if now>=end:return milliseconds
  time.sleep(min(.02,end-now))

def bite_trigger(config,drop,ring_score,submerged,splash=0,previous_ring_score=None):
 standard=drop>=config['drop_pixels'] and ring_score>=config['ring_min'] and submerged>=config['submerged_pixels']
 # Strong displacement and darkening can survive a brief blurred ring.
 fallback=(drop>=config.get('strong_drop_pixels',999) and
           ring_score>=config.get('strong_ring_min',1) and
           submerged>=config.get('strong_submerged_pixels',999) and splash>=config.get('strong_splash_min',0))
 blurred=(drop>=8 and ring_score>=config.get('blurred_ring_min',1) and submerged>=45 and splash>=config.get('blurred_splash_min',9999))
 splash_displacement=(drop>=config.get('splash_drop_pixels',999) and ring_score>=config.get('splash_ring_min',1) and splash>=config.get('splash_pixels',9999))
 occlusion=(previous_ring_score is not None and previous_ring_score>=config.get('occlusion_previous_min',2) and ring_score<=config.get('occlusion_ring_max',-1) and splash>=config.get('occlusion_splash_min',9999))
 return standard or fallback or blurred or splash_displacement or occlusion

def ring_reference(patch,config):
 if 'ring_anchor_roi' not in config:return crop(patch,config['ring_roi']).copy(),config['ring_roi'][:2]
 ax,ay,w,h=config['ring_anchor_roi']
 hsv=cv2.cvtColor(patch[ay:ay+h,ax:ax+w],cv2.COLOR_BGR2HSV)
 mask=cv2.inRange(hsv,np.array([0,0,config['ring_value_min']]),np.array([180,config['ring_saturation_max'],255]))
 _,_,stats,centers=cv2.connectedComponentsWithStats(mask)
 candidates=[]
 for (x,y,cw,ch,area),(cx,cy) in zip(stats[1:],centers[1:]):
  cx+=ax;cy+=ay
  if 3<=cw<=8 and 4<=ch<=10 and 6<=area<=40 and 24<=cx<=32:
   candidates.append((abs(cx-29),-area,round(cx)-5,round(cy)-5))
 if not candidates:raise RuntimeError('Silver bobber ring not isolated from background')
 _,_,x,y=min(candidates)
 return patch[y:y+11,x:x+11].copy(),[x,y]

def match(im,tpl,roi):
 x,y,w,h=roi
 scores=cv2.matchTemplate(im[y:y+h,x:x+w],tpl,cv2.TM_CCOEFF_NORMED)
 _,v,_,(px,py)=cv2.minMaxLoc(scores)
 return float(v),(x+px,y+py)

def green_loot_prefix(chat,cy):
 h,w=chat.shape[:2]
 mid=round((1-cy)*h)  # ui_ocr boxes use a bottom-origin y axis.
 band=chat[max(0,mid-round(.035*h)):min(h,mid+round(.035*h)),:round(.32*w)]
 if band.size==0:return False
 b,g,r=cv2.split(band)
 return int(np.count_nonzero((g>35)&(g>r*1.2)&(g>b*1.2)))>=300

def read_valid_fishing_frame(feed,config,box,deadline,stop_file,fault_log):
 reason='Invalid frame';attempts=0;woke=False;wake_until=0;last={}
 def record(event,**details):
  with fault_log.open('a') as stream:
   stream.write(json.dumps(dict(at=time.time(),event=event,**details))+'\n')
 while True:
  if time.monotonic()>deadline or stop_file.exists():raise RuntimeError('Cancelled or duration limit')
  im,age=feed.raw_frame();last=dict(age=round(age,3),shape=list(im.shape[:2]),mean=round(float(im.mean()),2),std=round(float(im.std()),2))
  if age>config['frame_max_age'] or im.shape[:2]!=tuple(reversed(config['frame_size'])):
   reason='Invalid frame'
  else:
   green=float(np.mean(crop(im,config['player_roi'])[:,:,1]));last['player_green']=round(green,2)
   if green>=config['player_green_min']:
    if woke:record('black_capture_recovered',**last)
    return im
   reason='Player frame unavailable'
   if last['mean']<1 and last['std']<1:
    if not woke:
     # Only a fully black capture authorizes this bounded screen wake.
     box.command('km.move(50,20)');woke=True;wake_until=time.monotonic()+5
     record('black_capture_wake_sent',**last)
    if time.monotonic()<wake_until:
     time.sleep(.25);continue
    reason='Black capture persisted after wake'
    break
  attempts+=1
  if attempts>=3:break
 record('capture_guard_stop',reason=reason,**last)
 raise RuntimeError(reason+' after bounded fresh-frame checks')

def select_bobber(candidates,config):
 valid=[c for c in candidates if c['score']>=config['candidate_min'] and c['old']<=config['old_candidate_max'] and c['score']-c['old']>=config['new_candidate_margin']]
 if not valid:raise RuntimeError('New own bobber not confirmed '+str(max(candidates,key=lambda c:c['score'],default=None)))
 if config.get('reject_ambiguous_candidates',0):
  best=max(valid,key=lambda c:c['score'])
  if any((c['x']-best['x'])**2+(c['y']-best['y'])**2>config.get('candidate_cluster_pixels',30)**2 for c in valid):
   raise RuntimeError('Multiple new bobbers; ownership ambiguous')
 return max(valid,key=lambda c:c['score'])

def receipts(im, folder, label, config=None):
 config=config or load_config()
 path=folder/(label+'-chat.png')
 chat=cv2.resize(crop(im,config["receipt_roi"]),None,fx=config["ocr_scale"],fy=config["ocr_scale"])
 if config.get('ocr_enhance',0):
  b,g,r=cv2.split(chat.astype(float))
  chat=255-np.clip(np.maximum((g-np.maximum(r,b))*6,(np.minimum(r,g)-90)*2),0,255).astype('uint8')
 cv2.imwrite(str(path),chat)
 proc=subprocess.run([str(ROOT/'ui_ocr')],input=str(path)+'\n',text=True,capture_output=True,check=True,timeout=config["ocr_timeout"])
 data=json.loads(proc.stdout);(folder/(label+'-ocr.json')).write_text(json.dumps(data,ensure_ascii=False))
 if config.get('ocr_enhance',0) and not any((''.join(r['text'].split()).startswith('你获得') or re.match(r'^[\[［「Ii1]?新鲜的',''.join(r['text'].split()))) and any(k in r['text'] for k in ('鱼','泥鲷','泥鯛')) for r in data.get('items',[])):
  x,y,w,h=config.get('ocr_tail_roi',[155,166,200,31]);scale=config['ocr_scale']
  tail=chat[round(y*scale):round((y+h)*scale),round(x*scale):round((x+w)*scale)]
  tail=cv2.copyMakeBorder(tail,50,50,50,50,cv2.BORDER_CONSTANT,value=255)
  tail_path=folder/(label+'-tail.png');cv2.imwrite(str(tail_path),tail)
  proc=subprocess.run([str(ROOT/'ui_ocr')],input=str(tail_path)+'\n',text=True,capture_output=True,check=True,timeout=config['ocr_timeout'])
  data=json.loads(proc.stdout);(folder/(label+'-tail-ocr.json')).write_text(json.dumps(data,ensure_ascii=False))
 items=[r for r in data.get('items',[]) if r.get('confidence',0)>=config['ocr_confidence']]
 rows=[]
 for item in sorted(items,key=lambda r:-(r.get('box',[0,0,0,0])[1]+r.get('box',[0,0,0,0])[3]/2)):
  x,y,w,h=item.get('box',[0,0,1,.01]);cy=y+h/2
  row=next((r for r in rows if abs(r['cy']-cy)<min(r['height'],h)*.5),None)
  if row is None:row=dict(cy=cy,height=h,items=[]);rows.append(row)
  row['items'].append((x,''.join(item['text'].split())))
 lines=[''.join(t for _,t in sorted(row['items'])) for row in rows]
 fish=[]
 for row,r in zip(rows,lines):
  personal=r.startswith('你获得')
  item_name=bool(re.match(r'^[\[［「Ii1]?新鲜的',r) and any(k in r for k in ('鱼','泥鲷','泥鯛')))
  if (personal and ('新鲜' in r or '鱼' in r or '泥鳅' in r or '泥鲷' in r)) or (item_name and (config.get('ocr_enhance',0) or green_loot_prefix(chat,row['cy']))):
   fish.append(r)
 skill=[int(m.group(1)) for r in lines if (m:=re.search(r'钓鱼技能提高到[了可]?(\d+)',r))]
 return dict(fish=fish,skill=max(skill,default=0),lines=lines)

def main(argv=None):
 args,config=parse_options(argv)
 if args.check_config:
  print(json.dumps(config,ensure_ascii=False,indent=2));return 0
 if not args.execute:raise SystemExit('必须显式 --execute 才能操作设备；--check-config 仅校验配置')
 out=args.output or ROOT/'runs'/('fishing-live-'+time.strftime('%Y%m%d-%H%M%S'))
 out=out.resolve();out.mkdir(parents=True,exist_ok=False)
 (out/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
 for name in ('fishing_trial.py','fishing_inventory.py','fishing_config.py','fishing_candidates.py','fishing_audio.py','fishing_signals.py'):
  (out/name).write_text((ROOT/name).read_text())
 (ROOT/'runs/current-fishing-live.txt').write_text(str(out))
 try:
  run_session(config,out)
 except BaseException as exc:
  status_path=out/'status.json'
  state=json.loads(status_path.read_text()) if status_path.exists() else dict(consecutive=0,history=[])
  state.update(state='cancelled' if isinstance(exc,KeyboardInterrupt) or (out/'STOP').exists() else 'failed',reason=str(exc))
  (out/'result.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
  status_path.write_text(json.dumps(state,ensure_ascii=False,indent=2))
  raise
 else:
  state=json.loads((out/'status.json').read_text())
  complete=state.get('bag_full',False) if config['stop_mode']=='bag_full' else state['consecutive']>=config['target_streak']
  state.update(state='completed' if complete else 'stopped',
               reason=('bag_full' if config['stop_mode']=='bag_full' else 'target_reached') if complete else 'cast_limit_or_unconfirmed')
  (out/'result.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
  (out/'status.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
  return 0


def run_session(config,out):
 print(str(out),flush=True)
 feed=Feed(config['obs_source']);vis=LootVision();templates=[cv2.imread(str(ROOT/n)) for n in config['bobber_templates']];tip=cv2.imread(str(ROOT/config['tooltip_template']))
 templates=[cv2.resize(t,None,fx=s,fy=s) for t in templates for s in config['template_scales']]
 deadline=time.monotonic()+config['max_seconds']
 audio=None
 if config.get('audio_record',0):
  from fishing_audio import FishingAudio
  audio=FishingAudio(out,json.loads((ROOT/'calibration/runtime.json').read_text())['audio']['device_uid'])
 streak=0;history=[];last_skill=0;missed=0
 inventory=FishingInventory(config) if config['stop_mode']=='bag_full' else None
 bag_state=None;bag_pixels=None
 def frame():
  return read_valid_fishing_frame(feed,config,box,deadline,out/'STOP',out/'capture-faults.jsonl')
 def save(path,im):cv2.imwrite(str(path),im)
 try:
  with KMBox(config['serial_port']) as box:
   if inventory:
    bag_state,bag_pixels=inventory.inspect(frame,box,out,'initial')
    if bag_state['empty']==0:
     (out/'status.json').write_text(json.dumps(dict(state='running',consecutive=0,history=[],bag_full=True,bag=bag_state)))
     return
   for cast in range(1,config['casts']+1):
    d=out/f'cast-{cast:02}';d.mkdir();events=(d/'events.jsonl').open('w')
    def log(**r):events.write(json.dumps(dict(at=time.monotonic(),**r))+'\n');events.flush()
    frame();box.command('km.move(%d,%d)' % tuple(config['cursor_clear_delta']));time.sleep(config['cursor_clear_seconds'])
    before=frame();save(d/'before.jpg',before);old_receipts=receipts(before,d,'before',config);last_skill=max(last_skill,old_receipts['skill'])
    full_before=full_notice(before,d,'before',config) if inventory else False
    box.tap(config['cast_hid'],config['key_duration_ms']);started=time.monotonic();log(event='cast_sent');time.sleep(config['cast_settle_seconds'])
    im=frame();save(d/'cast.jpg',im)
    candidates=propose(before,im,templates,config,deduplicate=bool(config.get('hover_scan',0)))
    (d/'candidates.json').write_text(json.dumps(candidates))
    if config.get('hover_scan',0):
     c=None;gear=cv2.imread(str(ROOT/config['gear_template']))
     for candidate in sorted(candidates,key=lambda c:c['score']-c['old'],reverse=True)[:config.get('hover_candidate_limit',6)]:
      hover,feedback=hover_candidate(candidate,frame,box,vis,gear,config,log)
      tipscore,_=match(hover,tip,config['tooltip_roi'])
      log(event='candidate_verification',candidate=candidate,feedback=feedback,tooltip=tipscore)
      if feedback['gear'] and tipscore>config['tooltip_min']:
       # Independent fresh frame must retain both local interaction feedback and tooltip.
       check=frame();x,y=candidate['x'],candidate['y']
       gs,_=match(check,gear,(x-45,y-45,110,110));ts,_=match(check,tip,config['tooltip_roi'])
       if gs>=config['gear_min'] and ts>config['tooltip_min']:c=candidate;break
     if c is None:raise RuntimeError('No interactable fishing bobber confirmed')
     x,y=c['x'],c['y'];positioned=True
     log(event='bobber',candidate=c,ownership='two_frame_gear_and_tooltip')
    else:
     c=select_bobber(candidates,config);log(event='bobber',candidate=c)
     x,y=c['x'],c['y'];positioned=False
     for n in range(config['position_attempts']):
      im=frame();tipscore,_=match(im,tip,config['tooltip_roi'])
      if tipscore>config['tooltip_min']:
       positioned=True;break
      cursor=vis.cursor(im,config['cursor_roi']);log(event='position',cursor=cursor,tooltip=tipscore)
      if not cursor:
       save(d/'cursor-failed.jpg',im)
       log(event='cursor_failed',candidate=getattr(vis,'last_cursor_candidate',None))
       raise RuntimeError('Unknown cursor')
      dx=x+config['cursor_target_offset'][0]-cursor['x'];dy=y+config['cursor_target_offset'][1]-cursor['y']
      if abs(dx)<config['position_tolerance'] and abs(dy)<config['position_tolerance']:break
      box.command(f"km.move({int(np.clip(round(dx/config['cursor_gain']),-config['cursor_step_limit'],config['cursor_step_limit']))},{int(np.clip(round(dy/config['cursor_gain']),-config['cursor_step_limit'],config['cursor_step_limit']))})");time.sleep(config['position_wait_seconds'])
    im=frame();save(d/'hover.jpg',im)
    tipscore,_=match(im,tip,config['tooltip_roi']);positioned=tipscore>config['tooltip_min']
    if not positioned:raise RuntimeError('Bobber tooltip not verified '+str(tipscore))
    log(event='hover_confirmed',score=tipscore)
    gear=cv2.imread(str(ROOT/config['gear_template']))
    gs,(gx,gy)=match(im,gear,(x-50,y-50,110,110))
    if gs<config['gear_min']:raise RuntimeError('Gear cursor localization failed')
    # Keep the pointer below the ring so the marker can actually be tracked.
    for correction in range(0 if config.get('clear_pointer_for_watch',0) else config['alignment_attempts']):
     dx=x+config['cursor_target_offset'][0]-(gx-config['gear_hotspot_offset'][0]);dy=y+config['cursor_target_offset'][1]-(gy-config['gear_hotspot_offset'][1])
     if abs(dx)<=config['position_tolerance'] and abs(dy)<=config['position_tolerance']:break
     box.command(f"km.move({round(dx/config['cursor_gain'])},{round(dy/config['cursor_gain'])})");time.sleep(config['alignment_wait_seconds'])
     im=frame();gs,(gx,gy)=match(im,gear,(x-50,y-50,110,110))
     if gs<config['gear_min']:raise RuntimeError('Lost bobber hover during pointer alignment')
    save(d/'aligned.jpg',im)
    visual_mask=np.ones((config['patch_offset'][3],config['patch_offset'][2]),bool)
    patch_x=x+config['patch_offset'][0];patch_y=y+config['patch_offset'][1]
    visual_mask[max(0,gy-10-patch_y):min(visual_mask.shape[0],gy+28-patch_y),max(0,gx-10-patch_x):min(visual_mask.shape[1],gx+28-patch_x)]=False
    log(event='cursor_mask',x=gx,y=gy,score=gs)
    if config.get('clear_pointer_for_watch',0):
     box.command('km.move(%d,%d)' % tuple(config['watch_cursor_delta']));time.sleep(.15)
     visual_mask[:]=True
    neighbors=[]
    if config.get('audio_record',0):
     from fishing_signals import LocalBiteObserver
     neighbors=[(candidate,LocalBiteObserver(candidate['x'],candidate['y'],config)) for candidate in candidates if (candidate['x']-x)**2+(candidate['y']-y)**2>25**2]
    neighbor_last={}
    patches=[];reel=False;i=0;ring=None;recent_ring_scores=[]
    while time.monotonic()-started<config['wait_seconds']:
     im=frame();patch=crop(im,[x+config['patch_offset'][0],y+config['patch_offset'][1],*config['patch_offset'][2:]])
     if i<config['baseline_frames'] or i%config['evidence_stride']==0:save(d/f'roi-{i:03}.png',patch)
     i+=1
     for ni,(neighbor,observer) in enumerate(neighbors):
      metric,neighbor_patch=observer.update(im)
      if metric and bite_trigger(config,**metric) and time.monotonic()-neighbor_last.get(ni,-100)>2:
       neighbor_last[ni]=time.monotonic()
       log(event='neighbor_visual_candidate',candidate=neighbor,metrics=metric,audio=audio.window(time.monotonic()) if audio else None)
       save(d/f'neighbor-{ni}-{i:03}.png',neighbor_patch)
     if len(patches)<config['baseline_frames']:
      patches.append(patch.astype(float))
      if len(patches)==config['reference_frame']:
       ring,ring_origin=ring_reference(patch,config);log(event='ring_reference',origin=ring_origin)
      continue
     base=np.median(patches[-config['median_frames']:],axis=0);delta=patch.astype(float)-base
     bright=((delta.mean(axis=2)>config['bright_delta'])&visual_mask)
     score=int((crop(bright,config['splash_roi']) if 'splash_roi' in config else bright).sum())
     submerged=int(crop(((delta.mean(axis=2)<-config['dark_delta'])&visual_mask),config['submerged_roi']).sum())
     patches.append(patch.astype(float));patches=patches[-config['baseline_frames']:]
     _,ring_score,_,(ring_x,ring_y)=cv2.minMaxLoc(cv2.matchTemplate(crop(patch,config['ring_search_roi']),ring,cv2.TM_CCOEFF_NORMED))
     drop=ring_y+config['ring_search_roi'][1]-ring_origin[1]
     if abs(ring_x+config['ring_search_roi'][0]-ring_origin[0])>config.get('ring_horizontal_tolerance',999):ring_score=0
     tips,_=match(im,tip,config['tooltip_roi'])
     log(event='watch',t=time.monotonic()-started,splash=score,submerged=submerged,drop=drop,ring_score=ring_score,tooltip=tips,audio=audio.window(time.monotonic()) if audio else None)
     prior_score=float(np.median(recent_ring_scores[-3:])) if recent_ring_scores else None
     recent_ring_scores.append(ring_score)
     if time.monotonic()-started>config['minimum_bite_seconds'] and bite_trigger(config,drop,ring_score,submerged,score,prior_score) and (config.get('clear_pointer_for_watch',0) or tips>config['tooltip_min']):
      signal_at=time.monotonic()
      if config.get('clear_pointer_for_watch',0):
       current,feedback=hover_candidate(c,frame,box,vis,gear,config,log)
       ts,_=match(current,tip,config['tooltip_roi'])
       if not feedback['gear'] or ts<=config['tooltip_min']:raise RuntimeError('Bite detected but own bobber interaction lost')
      delay=random_delay(config['reel_delay_ms_range'],out,deadline)
      box.command('km.click(1)');log(event='reel_sent',signal_at=signal_at,splash=score,random_delay_ms=round(delay,2),signal_to_ack_ms=round((time.monotonic()-signal_at)*1000,2));save(d/'bite.jpg',im);reel=True;break
     time.sleep(config['watch_seconds'])
    for n in range(config['receipt_frames']):
     im=frame()
     if n==config['receipt_frames']-1:save(d/f'after-{n:02}.jpg',im)
     time.sleep(config['receipt_wait_seconds'])
    new_receipts=receipts(im,d,'after',config)
    full_after=False
    if inventory and reel and not full_before and full_notice(im,d,'after',config):
     full_after=full_notice(frame(),d,'after-confirm',config)
    fresh_receipt=(len(new_receipts['fish'])>len(old_receipts['fish']) or new_receipts['skill']>last_skill or new_receipts['fish']!=old_receipts['fish'])
    changed=False
    if inventory:
     next_bag,next_pixels=inventory.inspect(frame,box,d,'after')
     changed=inventory_changed(bag_pixels,next_pixels)
     bag_state,bag_pixels=next_bag,next_pixels
     if reel and changed and not new_receipts['fish']:
      new_receipts=receipts(frame(),d,'after-delayed',config)
     # Item stacks can grow while chat is full and skill no longer rises.
     fresh_receipt=changed
    confirmed=bool(reel and new_receipts['fish'] and fresh_receipt)
    streak=streak+1 if confirmed else 0
    missed=0 if confirmed else missed+1
    result=dict(cast=cast,reel_sent=reel,confirmed=confirmed,consecutive=streak,before=old_receipts,after=new_receipts,inventory_changed=changed,game_inventory_full=full_after,elapsed_seconds=round(time.monotonic()-started,2))
    last_skill=max(last_skill,new_receipts['skill']);history.append(result)
    (d/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    bag_full=full_after or bool(bag_state and bag_state['empty']==0)
    (out/'status.json').write_text(json.dumps(dict(state='running',consecutive=streak,confirmed_total=sum(r['confirmed'] for r in history),history=history,bag_full=bag_full,bag=bag_state),ensure_ascii=False,indent=2))
    print(dict(cast=cast,reel_sent=reel,confirmed=confirmed,consecutive=streak,skill=last_skill,empty=bag_state['empty'] if bag_state else None,seconds=result['elapsed_seconds']),flush=True)
    if bag_full or (not inventory and streak>=config['target_streak']):events.close();break
    if not confirmed and (reel or not inventory or missed>=config.get('max_consecutive_misses',1)):events.close();break
    time.sleep(config['between_cast_seconds'])
    delay=random_delay(config['cycle_delay_ms_range'],out,deadline)
    log(event='between_cast_delay',random_delay_ms=round(delay,2));events.close()
 finally:
  feed.close()
  if audio:audio.close()
if __name__=='__main__':raise SystemExit(main())
