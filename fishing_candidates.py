"""Image-only multi-candidate proposal and bounded feedback hover scan."""
import time
import cv2
import numpy as np
from fishing_config import crop

def propose(before,after,templates,config,*,deduplicate=True):
    rx,ry,rw,rh=config['world_roi']; candidates=[]
    for tpl in templates:
        th,tw=tpl.shape[:2]
        scores=cv2.matchTemplate(crop(after,config['world_roi']),tpl,cv2.TM_CCOEFF_NORMED)
        for _ in range(config.get('candidate_peaks',1)):
            _,score,_,(lx,ly)=cv2.minMaxLoc(scores)
            if score<config['candidate_min']:break
            px,py=rx+lx,ry+ly
            old=float(cv2.matchTemplate(before[max(0,py-8):py+th+8,max(0,px-8):px+tw+8],tpl,cv2.TM_CCOEFF_NORMED).max())
            candidates.append(dict(score=float(score),old=old,x=px+tw//2,y=py+th//2))
            radius=config.get('candidate_nms_radius',max(th,tw))
            scores[max(0,ly-radius):ly+radius+1,max(0,lx-radius):lx+radius+1]=-1
    if not deduplicate:return candidates
    selected=[]
    for c in sorted(candidates,key=lambda c:-c['score']):
        if all((c['x']-p['x'])**2+(c['y']-p['y'])**2>config.get('candidate_merge_radius',18)**2 for p in selected):selected.append(c)
    return selected

def hover_candidate(candidate,frame,box,vis,gear,config,log):
    tx=candidate['x']+config['cursor_target_offset'][0];ty=candidate['y']+config['cursor_target_offset'][1]
    for attempt in range(config['position_attempts']):
        im=frame();cursor=None
        x,y=candidate['x'],candidate['y'];left=max(0,x-45);top=max(0,y-45)
        scores=cv2.matchTemplate(im[top:y+65,left:x+65],gear,cv2.TM_CCOEFF_NORMED)
        _,score,_,(gx,gy)=cv2.minMaxLoc(scores);gx+=left;gy+=top
        if score>=config['gear_min']:
            cursor=dict(x=gx-config['gear_hotspot_offset'][0],y=gy-config['gear_hotspot_offset'][1],kind='gear',score=score)
        if not cursor:cursor=vis.cursor(im,config['cursor_roi'])
        if not cursor:
            rx,ry,rw,rh=config['cursor_roi']
            _,global_score,_,(cx,cy)=cv2.minMaxLoc(cv2.matchTemplate(im[ry:ry+rh,rx:rx+rw],gear,cv2.TM_CCOEFF_NORMED))
            if global_score>=config['gear_min']:
                cursor=dict(x=cx+rx-config['gear_hotspot_offset'][0],y=cy+ry-config['gear_hotspot_offset'][1],kind='gear',score=float(global_score))
            else:raise RuntimeError('Unknown cursor during candidate scan')
        dx,dy=tx-cursor['x'],ty-cursor['y']
        log(event='candidate_hover',candidate=candidate,attempt=attempt,cursor=cursor,gear_score=float(score),dx=dx,dy=dy)
        if ((score>=config['gear_min'] and abs(dx)<=config.get('hover_accept_radius',3) and abs(dy)<=config.get('hover_accept_radius',3)) or
            (abs(dx)<=config['position_tolerance'] and abs(dy)<=config['position_tolerance'])):
            return im,dict(gear=score>=config['gear_min'],score=float(score),x=gx,y=gy)
        limit=config['cursor_step_limit'];gain=config['cursor_gain']
        box.command(f'km.move({int(np.clip(round(dx/gain),-limit,limit))},{int(np.clip(round(dy/gain),-limit,limit))})')
        time.sleep(config['position_wait_seconds'])
    raise RuntimeError('Candidate pointer positioning limit')
