"""Shared BagVision adapter for raw-frame fishing, with stable counts and item deltas."""
import json,time,subprocess
import cv2
import numpy as np
from fishing_config import ROOT
from interaction_vision import BagVision

def full_notice(im,folder,label,config):
    x,y,w,h=config.get('inventory_full_roi',[800,140,400,80])
    patch=im[y:y+h,x:x+w]
    b,g,r=cv2.split(patch.astype(float))
    if int(((r>80)&(r>g*1.5)&(r>b*1.5)).sum())<20:return False
    path=folder/(label+'-full-message.png')
    cv2.imwrite(str(path),cv2.resize(patch,None,fx=2,fy=2))
    proc=subprocess.run([str(ROOT/'ui_ocr')],input=str(path)+'\n',capture_output=True,text=True,check=True,timeout=5)
    data=json.loads(proc.stdout)
    (folder/(label+'-full-message.json')).write_text(json.dumps(data,ensure_ascii=False))
    text=''.join(''.join(r['text'].split()) for r in data.get('items',[]))
    return '物品栏已满' in text or '背包已满' in text

class FishingInventory:
    def __init__(self,config):
        self.config=config
        self.layout=json.loads((ROOT/config['bag_layout']).read_text())
        self.vision=BagVision(self.layout)

    def inspect(self,frame,box,folder,label):
        # Pointer remains in the central water, away from inventory slots.
        opened=False
        try:
            im=frame();r=self.vision.observe(im)
            if not r['open']:
                box.tap(self.config['bag_hid'],80);opened=True
                time.sleep(self.config['bag_settle_seconds'])
            samples=[];stable=0
            for attempt in range(2):
                last=None;stable=0
                for _ in range(10):
                    im=frame();r=self.vision.observe(im)
                    signature=tuple(s['state'] for s in r.get('slots',[]))
                    stable=stable+1 if r.get('valid') and signature==last else int(r.get('valid',False))
                    last=signature;samples.append(r)
                    if stable>=3:break
                    time.sleep(.08)
                if stable>=3:break
                if attempt==0 and r.get('open'):
                    cv2.imwrite(str(folder/(label+'-bag-obstructed.jpg')),im)
                    box.tap(self.config['bag_hid'],80);time.sleep(self.config['bag_settle_seconds'])
                    if self.vision.observe(frame())['open']:
                        raise RuntimeError('Combined bag did not close during recovery')
                    box.tap(self.config['bag_hid'],80);time.sleep(self.config['bag_settle_seconds'])
                    (folder/(label+'-bag-recovery.json')).write_text(json.dumps(dict(reason='unstable_slots',action='close_reopen',attempt=1)))
                else:break
            if stable<3:
                cv2.imwrite(str(folder/(label+'-bag-failed.jpg')),im)
                raise RuntimeError('Combined bag count uncertain')
            pixels=[];p=self.layout
            for slot in r['slots']:
                x=round(p['origin'][0]+slot['col']*p['step'][0]);y=round(p['origin'][1]+slot['row']*p['step'][1]);n=p['size']
                pixels.append(im[y+3:y+n-3,x+3:x+n-3].copy())
            r.update(verified=True,stable_frames=3)
            (folder/(label+'-bag.json')).write_text(json.dumps(r,indent=2))
            cv2.imwrite(str(folder/(label+'-bag.jpg')),im)
            return r,np.array(pixels)
        finally:
            # Close only a positively detected open bag, including one left open by user.
            im=frame()
            if self.vision.observe(im)['open']:
                box.tap(self.config['bag_hid'],80);time.sleep(.12)
                if self.vision.observe(frame())['open']:
                    raise RuntimeError('Combined bag did not close')
            elif opened:
                raise RuntimeError('Combined bag header not recognized after opening')


def inventory_changed(before,after,delta=30,minimum_pixels=12):
    if before.shape!=after.shape:return False
    # JPEG noise is rejected; quantity digits or a new item must visibly change.
    changed=(np.abs(after.astype(float)-before.astype(float)).max(axis=3)>delta)
    return bool(np.any(changed.sum(axis=(1,2))>=minimum_pixels))
