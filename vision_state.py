"""Calibrated pixel/template observations. Uncalibrated frames never authorize input."""
import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Observation:
    valid: bool = False
    reason: str = 'uncalibrated'
    player_hp: float = 0.0
    player_mana: float = 0.0
    player_mana_lower_bound: bool = False
    target: bool = False
    target_hp: float = 0.0
    target_allowed: bool = False
    casting: bool = False
    xp_visible: bool = False
    bearing: float = None
    target_point: tuple = None
    in_combat: bool = False
    maintenance_buff_present: bool = None
    opener_in_range: bool = None
    casting_known: bool = False


class Vision:
    def __init__(self, profile_path):
        path = Path(profile_path)
        self.profile = json.loads(path.read_text())
        self.templates = {}
        self.target_label = None
        self.previous_experience = None
        self.percent_readers = {}
        self.status_text_unknown = False
        for name, spec in self.profile['templates'].items():
            im = cv2.imread(str(path.parent / spec['file']))
            if im is None or im.std() < 2:
                raise ValueError(f'Missing or featureless template: {name}')
            self.templates[name] = im
        for required in ('game_anchor', 'target_anchor', 'allowed_name', 'xp'):
            if required not in self.templates:
                raise ValueError(f'Missing calibrated template: {required}')
        for required in ('player_hp', 'player_mana', 'target_hp'):
            if required not in self.profile['bars']:
                raise ValueError(f'Missing calibrated bar: {required}')

    def crop(self, frame, rect):
        x, y, w, h = map(int, rect)
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > frame.shape[1] or y+h > frame.shape[0]:
            raise ValueError('Calibration rectangle is outside frame')
        return frame[y:y+h, x:x+w]

    def score(self, frame, name):
        spec = self.profile['templates'][name]
        patch = self.crop(frame, spec['roi'])
        template = self.templates[name]
        if patch.shape[0] < template.shape[0] or patch.shape[1] < template.shape[1]:
            raise ValueError(f'Template larger than search ROI: {name}')
        scores = cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED)
        return float(scores.max()) if np.isfinite(scores).all() else -1.0

    def matches(self, frame, name):
        return self.score(frame, name) >= self.profile['templates'][name].get('threshold', .92)

    def opener_range(self,frame,target):
        roi=self.profile.get('opener_range_roi')
        if not target or not roi:return None
        hsv=cv2.cvtColor(self.crop(frame,roi),cv2.COLOR_BGR2HSV)
        red=((hsv[:,:,0]<10)|(hsv[:,:,0]>170))&(hsv[:,:,1]>130)&(hsv[:,:,2]>90)
        pale=(hsv[:,:,1]<100)&(hsv[:,:,2]>150)
        if red.sum()>=3:return False
        if pale.sum()>=3:return True
        return None

    def allowed_target(self, frame, target):
        if not target:
            self.target_label = None
            return False
        scores = {name:self.score(frame,name) for name in self.templates if name.startswith('allowed_name')}
        name = max(scores,key=scores.get)
        threshold = self.profile['templates'][name].get('threshold',.92)
        if name == self.target_label:
            threshold -= .08  # Same known name; tolerate combat shading, not a new label.
        if scores[name] >= threshold:
            self.target_label = name
            return True
        return False

    def bar(self, frame, name):
        spec = self.profile['bars'][name]
        for variant in spec.get('variants',[]):
            if self.matches(frame,variant['anchor']):
                spec=dict(spec,roi=variant['roi']);break
        overlay=spec.get('status_text')
        if overlay:
            from local_status_text import PercentReader, text_present
            patch=self.crop(frame,overlay['text_roi'])
            if text_present(self.crop(frame,overlay.get('detect_roi',overlay['text_roi']))):
                if 'clear_roi' in overlay:
                    spec=dict(spec,roi=overlay['clear_roi'])
                else:
                    if name not in self.percent_readers:
                        self.percent_readers[name]=PercentReader(full_template=overlay.get('full_template'))
                    reader=self.percent_readers[name]
                    value=reader.read(patch)
                    if value is not None and 'verify_roi' in overlay:
                        x,y,w,h=overlay['verify_roi']
                        hsv=cv2.cvtColor(self.crop(frame,[x,y,w,h]),cv2.COLOR_BGR2HSV)
                        colored=cv2.inRange(hsv,np.array(spec['hsv_low']),np.array(spec['hsv_high']))>0
                        observed=float((colored.mean(axis=0)>=.5).mean())
                        bx,_,bw,_=spec['roi']
                        expected=max(0,min(1,(bx+value*bw-x)/w))
                        if abs(observed-expected)>.18:
                            value=None
                    if value is None and overlay.get('allow_color_lower_bound'):
                        x,y,w,h=overlay['verify_roi'];bx,_,bw,_=spec['roi']
                        hsv=cv2.cvtColor(self.crop(frame,[x,y,w,h]),cv2.COLOR_BGR2HSV)
                        blue=cv2.inRange(hsv,np.array(spec['hsv_low']),np.array(spec['hsv_high']))>0
                        if bx<=x<x+w<=bx+bw and ((blue.mean(axis=0)>=.5).mean()>=.975):
                            self.mana_lower_bound=True
                            return float(np.floor(((x+w-bx)/bw-.03)*20)/20)
                    if value is None:
                        self.status_text_unknown=True
                        return 0.0
                    return value
        hsv = cv2.cvtColor(self.crop(frame, spec['roi']), cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(spec['hsv_low']), np.array(spec['hsv_high']))
        columns = (mask > 0).mean(axis=0) >= spec.get('column_fraction', 0.5)
        # Bars must fill from the left. Isolated matching scenery on the right
        # cannot inflate the reading when the bar is mostly empty.
        positions = np.flatnonzero(columns)
        if not len(positions):
            return 0.0
        if positions[0] > 2:
            # Player damage text can cover the first few pixels. Require a
            # substantial visible fill; do not rescue an empty/low-health bar.
            if not (name == 'player_hp' and positions[0] <= 10
                    and positions[-1] >= len(columns)*.4
                    and len(positions)/(positions[-1]-positions[0]+1) >= .85):
                return 0.0
        end = int(positions[0])
        for pos in positions[1:]:
            if pos - end > 3:
                break
            end = int(pos)
        return (end + 1) / len(columns)

    def bearing(self, frame):
        point=self.world_target_point(frame)
        return point[0]-self.profile.get('player_center_x',1004) if point else None

    def world_target_point(self, frame):
        # Only the open world region; excludes portraits, map, quests and chat.
        roi = self.profile.get('world_roi')
        if not roi:
            return None
        patch = self.crop(frame, roi)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        if self.profile.get('bearing_mode') == 'nameplate':
            yellow = cv2.inRange(hsv, np.array([15,100,120]), np.array([40,255,255]))
            red = cv2.inRange(hsv, np.array([0,130,110]), np.array([9,255,255]))
            mask = yellow | red
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            candidates=[]
            for contour in contours:
                x,y,w,h=cv2.boundingRect(contour)
                if 75 <= w <= 125 and 4 <= h <= 14 and cv2.contourArea(contour)/(w*h)>.65:
                    candidates.append((roi[0]+x+w/2,roi[1]+y+h/2))
            return candidates[0] if len(candidates)==1 else None
        mask = cv2.inRange(hsv, np.array([15, 80, 160]), np.array([40, 255, 255]))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 9), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            x,y,w,h = cv2.boundingRect(contour)
            if 12 <= w <= 110 and 3 <= h <= 20 and w > h*1.5:
                candidates.append((roi[0]+x+w/2,roi[1]+y+h/2))
        if len(candidates) != 1:
            return None
        return candidates[0]

    def combat(self, frame):
        roi = self.profile.get('combat_roi')
        if not roi:
            return False
        hsv = cv2.cvtColor(self.crop(frame, roi), cv2.COLOR_BGR2HSV)
        red = ((hsv[:,:,0] < 10) | (hsv[:,:,0] > 170)) & (hsv[:,:,1] > 140) & (hsv[:,:,2] > 90)
        if float(red.mean()) <= self.profile.get('combat_red_ratio_min', .03):
            return False
        # On the current paladin layout the combat flare enters through the top
        # edge of this ROI.  The lower-left portion is transparent world space;
        # a red terrain/object there previously looked like combat after a turn.
        top_rows = self.profile.get('combat_top_rows')
        if top_rows is not None:
            top_rows = max(1, min(int(top_rows), red.shape[0]))
            if int(red[:top_rows].sum()) < self.profile.get('combat_top_red_min', 1):
                return False
        return True

    def observe(self, frame):
        if [frame.shape[1], frame.shape[0]] != self.profile['frame_size']:
            return Observation(reason='frame_size_changed')
        if not self.matches(frame, 'game_anchor'):
            return Observation(reason='game_anchor_missing')
        for name in self.profile.get('blocking_templates', []):
            if self.matches(frame, name):
                return Observation(reason=f'blocking_ui:{name}')
        self.status_text_unknown=False
        self.mana_lower_bound=False
        hp = self.bar(frame, 'player_hp')
        if hp <= 0:
            return Observation(reason='player_health_unreadable_or_zero')
        mana = self.bar(frame, 'player_mana')
        target = self.matches(frame, 'target_anchor')
        if not target:
            target = any(self.matches(frame,n) for n in self.templates if n.startswith('target_frame_edge')) and self.bar(frame, 'target_hp') > .02
        if not target:
            # Frame presence must not depend on the remaining health fill.
            # Structural anchors are independently calibrated against empty UI.
            target=any(self.matches(frame,n) for n in self.templates if n.startswith('target_presence_'))
        if not target and self.profile.get('target_structure_roi'):
            # Red combat outline changes brightness. Combine spatial structure
            # with an independently matched allowed name, never health fill.
            hsv=cv2.cvtColor(self.crop(frame,self.profile['target_structure_roi']),cv2.COLOR_BGR2HSV)
            red=((hsv[:,:,0]<10)|(hsv[:,:,0]>170))&(hsv[:,:,1]>100)&(hsv[:,:,2]>40)
            named=any(self.matches(frame,n) for n in self.templates if n.startswith('allowed_name'))
            target=bool(named and red.mean()>=.35 and (red.mean(axis=1)>.6).sum()>=3)
        xp_visible=self.matches(frame,'xp')
        if 'experience' in self.profile['bars']:
            experience=self.bar(frame,'experience')
            # Only small positive changes count; initialization and level wraps
            # cannot fabricate experience. Policy still requires recent damage.
            xp_visible=(self.previous_experience is not None and
                        .004 < experience-self.previous_experience < .15)
            if not self.status_text_unknown:
                self.previous_experience=experience
        target_point=self.world_target_point(frame) if target else None
        return Observation(valid=not self.status_text_unknown,
            reason='status_text_unreadable' if self.status_text_unknown else 'ok', player_hp=hp,
            player_mana=mana, player_mana_lower_bound=self.mana_lower_bound, target=target,
            target_hp=self.bar(frame, 'target_hp') if target else 0,
            target_allowed=self.allowed_target(frame,target),
            casting=self.bar(frame, 'casting') > 0.02 if 'casting' in self.profile['bars'] else False,
            casting_known='casting' in self.profile['bars'],
            xp_visible=xp_visible,
            target_point=target_point,
            bearing=target_point[0]-self.profile.get('player_center_x',1004) if target_point else None, in_combat=self.combat(frame),
            opener_in_range=self.opener_range(frame,target),
            maintenance_buff_present=any(self.matches(frame,n) for n in self.templates if n=='maintenance_buff' or n.startswith('maintenance_buff_')) if self.profile.get('maintenance_buff_monitor',False) and 'maintenance_buff' in self.templates else None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('image')
    args = parser.parse_args()
    frame = cv2.imread(args.image)
    if frame is None:
        raise SystemExit('Cannot read image')
    print(json.dumps(asdict(Vision(args.profile).observe(frame)), ensure_ascii=False))
