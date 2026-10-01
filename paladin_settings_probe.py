"""Supervised single UI action for Paladin settings, with fresh cursor feedback."""
import argparse,json,time
from pathlib import Path
import cv2
from vision_feed import Feed
from interaction_vision import LootVision
from kmbox_tap import KMBox
from control_lock import controller_lock

p=argparse.ArgumentParser();p.add_argument('--click',nargs=2,type=int);p.add_argument('--drag-to',nargs=2,type=int);p.add_argument('--hover',action='store_true');p.add_argument('--button',type=int,choices=(0,1),default=0);p.add_argument('--key',type=int);p.add_argument('--wheel',type=int);p.add_argument('--output',required=True);a=p.parse_args()
with controller_lock():
 f=Feed()
 try:
  with KMBox() as b:
   if a.click:
    x,y=a.click;assert 375<x<1640 and 150<y<940
    profile=json.loads(Path('classes/paladin/vision/profile.json').read_text())
    v=LootVision(profile.get('cursor_templates'));previous=None
    for _ in range(50):
     im,age=f.frame()
     if previous is None:
      previous=v.cursor(im)
      if previous is None:raise RuntimeError('Cursor unavailable')
      continue
     ox=max(375,previous['x']-150);oy=max(150,previous['y']-150)
     cur=v.cursor(im,roi=(ox,oy,min(300,1640-ox),min(300,1000-oy)))
     if cur is None:cur=v.cursor(im)
     previous=cur
     if cur is None or age>.5:raise RuntimeError('Cursor/frame unavailable')
     dx,dy=x-cur['x'],y-cur['y']
     if abs(dx)<4 and abs(dy)<4:
      
      if not a.hover and not a.drag_to:b.command(f'km.click({a.button})')
      break
     mx,my=f.mouse_delta(max(-50,min(50,round(dx/3))),max(-50,min(50,round(dy/3))))
     b.command(f'km.move({mx},{my})');time.sleep(.3)
    else:raise RuntimeError('Pointer convergence limit')
    if a.drag_to:
     x,y=a.drag_to;assert 375<x<1640 and 150<y<940
     try:
      im,age=f.frame();assert age<.5
      b.command('km.left(1)')
      for _ in range(80):
       im,age=f.frame();cur=v.cursor(im)
       if cur is None or age>.5:raise RuntimeError('Drag cursor/frame unavailable')
       dx,dy=x-cur['x'],y-cur['y']
       if abs(dx)<4 and abs(dy)<4:break
       mx,my=f.mouse_delta(max(-40,min(40,round(dx/3))),max(-40,min(40,round(dy/3))))
       b.command(f'km.move({mx},{my})');time.sleep(.2)
      else:raise RuntimeError('Drag convergence limit')
     finally:b.command('km.left(0)')
   elif a.wheel:
    assert -30<=a.wheel<=30
    f.frame();b.command(f'km.wheel({a.wheel})')
   elif a.key:
    f.frame();b.tap(a.key,80)
   time.sleep(.3);im,_=f.frame();cv2.imwrite(a.output,im)
   print(json.dumps({'click':a.click,'key':a.key,'output':a.output}))
 finally:f.close()
