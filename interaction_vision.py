"""Shared calibrated cursor, corpse and backpack recognition; no hardware."""
from pathlib import Path
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parent


class LootVision:
    def __init__(self, overrides=None, message_template=None):
        self.templates = {n: cv2.imread(str(ROOT/'calibration'/f'cursor_{n}.png'))
                          for n in ('hand', 'loot', 'skin', 'attack', 'attack_dim')}
        self.prefix = cv2.imread(str(ROOT/(message_template or 'calibration/loot_message.png')))
        if any(v is None for v in self.templates.values()) or self.prefix is None:
            raise ValueError('Missing loot calibration')
        # The hardware cursor keeps its desktop pixel size when game UI grows.
        # After window normalization its size can therefore differ from the UI.
        self.templates.update({name+'_small':cv2.resize(t,None,fx=.8,fy=.8)
                               for name,t in list(self.templates.items())})
        for name,path in (overrides or {}).items():
            if name not in self.templates and name not in ('herb','herb_small','mine','mine_small','talk','talk_small'):
                raise ValueError('Unknown cursor template: '+name)
            for i,p in enumerate(path if isinstance(path,list) else [path]):
                template=cv2.imread(str(ROOT/p))
                if template is None:
                    raise ValueError('Missing cursor template: '+p)
                self.templates[name if i==0 else name+'__'+str(i)]=template

    def cursor(self, frame, roi=(375,150,1250,850)):
        previous=getattr(self,'previous_cursor',None)
        if previous:
            x,y,w,h=roi
            left=max(x,previous['x']-70);top=max(y,previous['y']-70)
            right=min(x+w,previous['x']+105);bottom=min(y+h,previous['y']+105)
            if right>left and bottom>top:
                found=self._find_cursor(frame,(left,top,right-left,bottom-top))
                if found:self.previous_cursor=found;return found
        found=self._find_cursor(frame,roi)
        self.previous_cursor=found
        return found

    def _find_cursor(self, frame, roi):
        ox,oy,w,h=roi
        patch = frame[oy:oy+h,ox:ox+w]
        best = None
        preferred=getattr(self,'preferred_cursor_scale',1.)
        previous_variant=getattr(self,'preferred_cursor_variant',None)
        ordered=sorted(self.templates.items(),key=lambda pair:(pair[0]!=previous_variant,(.8 if pair[0].split('__')[0].endswith('_small') else 1.)!=preferred))
        for variant, template in ordered:
            kind=variant.split('__')[0]
            hsv = cv2.cvtColor(template, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array([0,70,90]), np.array([40,255,255]))
            mask = cv2.dilate(mask, np.ones((3,3), np.uint8))
            result = cv2.matchTemplate(patch, template, cv2.TM_SQDIFF_NORMED, mask=mask)
            result = np.nan_to_num(result, nan=1, posinf=1, neginf=1)
            distance, _, location, _ = cv2.minMaxLoc(result)
            score = 1-distance
            item = dict(kind=kind.removesuffix('_small'), score=score, x=location[0]+ox, y=location[1]+oy,
                        cursor_scale=.8 if kind.endswith('_small') else 1.,template_variant=variant)
            if best is None or score > best['score']:
                best = item
            if score>=.96 or (variant==previous_variant and score>=.94):
                self.preferred_cursor_variant=variant
                self.preferred_cursor_scale=item['cursor_scale']
                return item
        self.last_cursor_candidate=best
        if best['score'] >= .92:
            self.preferred_cursor_variant=best['template_variant']
            self.preferred_cursor_scale=best['cursor_scale']
            return best
        # Snow/background pixels around the bag cursor can vary. Rescue only
        # a near-match whose colored cursor core independently matches tightly.
        if best['kind']=='loot' and best['score']>=.90 and best.get('cursor_scale',1)==1:
            template=self.templates['loot']
            core=cv2.inRange(cv2.cvtColor(template,cv2.COLOR_BGR2HSV),
                             np.array([0,70,90]),np.array([40,255,255]))
            x,y=best['x'],best['y'];th,tw=template.shape[:2]
            score=1-float(cv2.matchTemplate(frame[y:y+th,x:x+tw],template,
                                          cv2.TM_SQDIFF_NORMED,mask=core)[0,0])
            if np.isfinite(score) and score>=.965:
                return dict(best,core_score=score,evidence='outline_and_color_core')
        return None

    def candidates(self, frame):
        patch = frame[450:875, 790:1390]
        found = []
        for path in sorted((ROOT/'calibration').glob('corpse_*.png')):
            original = cv2.imread(str(path))
            for scale in (.65, .8, 1., 1.2, 1.4, 1.6):
                template = cv2.resize(original, None, fx=scale, fy=scale)
                scores = cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED)
                _, score, _, (x,y) = cv2.minMaxLoc(scores)
                if score >= .65:
                    found.append(dict(x=x+790+template.shape[1]//2,
                                      y=y+450+template.shape[0]//2, score=score))
        # Sparkles propose locations only; a loot cursor still authorizes clicks.
        cursor = self.cursor(frame)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([12,10,210]), np.array([65,210,255]))
        _, _, stats, centers = cv2.connectedComponentsWithStats(mask)
        for stat, center in zip(stats[1:], centers[1:]):
            sx, sy = center + [790,450]
            if not 2 <= stat[4] <= 35:
                continue
            if cursor and abs(sx-cursor['x'])<40 and abs(sy-cursor['y'])<40:
                continue
            found.append(dict(x=int(sx),y=int(sy)+15,score=.64))
        selected = []
        for item in sorted(found, key=lambda v:-v['score']):
            if not 450 <= item['x'] <= 1380 or not 260 <= item['y'] <= 860:
                continue
            if all(abs(item['x']-p['x'])+abs(item['y']-p['y'])>40 for p in selected):
                selected.append(item)
        return selected[:6]

    def sparkle_candidates(self, frames):
        """Temporal yellow sparkle clusters propose hover points, never clicks."""
        if len(frames) < 3:
            return []
        first=cv2.resize(cv2.cvtColor(frames[0][450:875,790:1390],cv2.COLOR_BGR2GRAY),(40,28))
        last=cv2.resize(cv2.cvtColor(frames[-1][450:875,790:1390],cv2.COLOR_BGR2GRAY),(40,28))
        if np.mean(np.abs(first.astype(float)-last.astype(float)))>12:
            return []  # Camera/scene movement invalidates the shared positions.
        masks=[]
        for frame in frames:
            patch=frame[450:875,790:1390]
            hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
            bright=cv2.inRange(hsv,np.array([12,10,210]),np.array([65,255,255]))
            count,labels,stats,_=cv2.connectedComponentsWithStats(bright)
            mask=np.zeros(bright.shape,np.uint8)
            for i in range(1,count):
                if 2<=stats[i,cv2.CC_STAT_AREA]<=35:
                    mask[labels==i]=1
            masks.append(mask)
        stack=np.stack(masks)
        # Static stars, text and bright terrain cannot create a temporal proposal.
        flicker=((stack.max(axis=0)>0)&(stack.min(axis=0)==0)).astype(np.uint8)
        groups=cv2.dilate(flicker,np.ones((25,25),np.uint8))
        count,labels,stats,_=cv2.connectedComponentsWithStats(groups)
        result=[]
        cursor=self.cursor(frames[-1])
        for i in range(1,count):
            x,y,w,h,_=stats[i]
            if w>100 or h>110:
                continue
            region=labels==i
            support=sum(bool(np.any(mask[region])) for mask in masks)
            yy,xx=np.where(region & (flicker>0))
            if support<2 or len(xx)<6:
                continue
            px=int(np.median(xx))+790;py=int(np.median(yy))+450+15
            if not 450<=px<=1380 or not 260<=py<=860:
                continue
            if cursor and abs(px-cursor['x'])<45 and abs(py-cursor['y'])<45:
                continue
            result.append(dict(x=px,y=py,score=.70,source='temporal_sparkles',
                               frames_supported=support,changing_pixels=len(xx)))
        return sorted(result,key=lambda p:(-p['frames_supported'],-p['changing_pixels']))[:6]

    def loot_messages(self, frame):
        # Count matching green loot prefixes, suppress adjacent template peaks.
        patch = frame[690:827, 398:778]
        scores = cv2.matchTemplate(patch, self.prefix, cv2.TM_CCOEFF_NORMED)
        peaks = []
        while scores.size:
            _, score, _, (x,y) = cv2.minMaxLoc(scores)
            if score < .83:
                break
            peaks.append(y)
            scores[max(0,y-10):y+11, :] = -1
        return len(peaks)


class BagVision:
    def __init__(self, layout=None):
        self.layout=layout
        if layout:
            self.class_header=cv2.imread(str(ROOT/layout['header']))
            self.class_empty=cv2.imread(str(ROOT/layout['empty']))
            self.class_empties=[self.class_empty]+[cv2.imread(str(ROOT/n)) for n in layout.get('empty_variants',[])]
        self.header=cv2.imread(str(ROOT/'calibration'/'bag_header.png'))
        self.empty=cv2.imread(str(ROOT/'calibration'/'bag_empty.png'))
        self.night_empty=cv2.imread(str(ROOT/'calibration'/'bag_empty_night.png'))
        self.night_header=cv2.imread(str(ROOT/'calibration'/'bag_header_night.png'))
        if self.header is None or self.empty is None:
            raise ValueError('Missing backpack calibration')

    def observe(self,frame):
        if frame.shape[:2]!=(1080,1920):
            return dict(open=False,valid=False,reason='frame_size')
        if self.layout:
            return self.observe_layout(frame)
        score=float(cv2.matchTemplate(frame[607:631,1529:1612],self.header,cv2.TM_CCOEFF_NORMED).max())
        night=False
        if score<.9:
            if self.night_header is not None:
                alternate=float(cv2.matchTemplate(frame[607:634,1490:1635],self.night_header,cv2.TM_CCOEFF_NORMED).max())
                if alternate>=.9:
                    night=True
                    if self.night_empty is None:
                        return dict(open=True,valid=False,reason='missing_backpack_slot_template',header_score=alternate)
            if not night:
                return dict(open=False,valid=False,reason='backpack_not_visible',header_score=score)
        # Player hover cards can obscure slots while the cursor still looks like a hand.
        strip=cv2.cvtColor(frame[869:880,1400:1640],cv2.COLOR_BGR2HSV)
        green=cv2.inRange(strip,np.array([35,120,60]),np.array([90,255,255]))
        _,_,components,_=cv2.connectedComponentsWithStats(green)
        if any(w>=60 and h<=8 and area>w*h*.5 for x,y,w,h,area in components[1:]):
            return dict(open=True,valid=False,reason='hover_tooltip_obstruction')
        slots=[]
        for row in range(5):
            for col in range(4):
                x,y=1489+36*col,673+36*row
                if night:
                    x,y=round(1487+36.5*col),round(673+36.5*row)
                size=34 if night else 33
                patch=frame[y:y+size,x:x+size]
                template=self.night_empty if night else self.empty
                score=float(cv2.matchTemplate(patch,template,cv2.TM_CCOEFF_NORMED).max())
                state='empty' if score>=.72 else 'occupied' if score<.55 else 'unknown'
                slots.append(dict(row=row,col=col,state=state,empty_score=score))
        counts={state:sum(s['state']==state for s in slots) for state in ('empty','occupied','unknown')}
        return dict(open=True,valid=counts['unknown']==0,scope='calibrated_backpack_only',
                    slots=slots,capacity=20,**counts)


    def observe_layout(self,frame):
        p=self.layout
        x,y,w,h=p['header_roi']
        score=float(cv2.matchTemplate(frame[y:y+h,x:x+w],self.class_header,cv2.TM_CCOEFF_NORMED).max())
        if score<.9:
            return dict(open=False,valid=False,reason='backpack_not_visible',header_score=score)
        slots=[]
        for row in range(p['rows']):
            for col in range(p['cols']):
                if [row,col] in p.get('excluded_slots',[]):
                    continue
                x=round(p['origin'][0]+col*p['step'][0]); y=round(p['origin'][1]+row*p['step'][1]); n=p['size']
                score=max(float(cv2.matchTemplate(frame[y-2:y+n+2,x-2:x+n+2],t,cv2.TM_CCOEFF_NORMED).max()) for t in self.class_empties)
                state='empty' if score>=.72 else 'occupied' if score<.55 else 'unknown'
                slots.append(dict(row=row,col=col,state=state,empty_score=score))
        counts={state:sum(s['state']==state for s in slots) for state in ('empty','occupied','unknown')}
        unknown_slots=[[s['row'],s['col']] for s in slots if s['state']=='unknown']
        return dict(open=True,valid=not unknown_slots,
                    reason='backpack_slots_unreadable' if unknown_slots else 'ok',
                    unknown_slots=unknown_slots,scope='calibrated_combined_backpack',
                    slots=slots,capacity=len(slots),**counts)
