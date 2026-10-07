"""Low-rate local visual OCR with bounded, timestamped combat evidence."""
import json,subprocess,tempfile,time,threading,unicodedata,re
from pathlib import Path
import cv2,numpy as np
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor

def read_lines(patch):
 with tempfile.TemporaryDirectory(prefix='wow-combat-text-') as d:
  p=Path(d)/'log.png';cv2.imwrite(str(p),cv2.resize(patch,None,fx=3,fy=3))
  r=subprocess.run([str(Path(__file__).with_name('ui_ocr'))],input=str(p)+'\n',capture_output=True,text=True,timeout=2,check=True)
  return joined_lines(json.loads(r.stdout)['items'])

def joined_lines(items):
 # Vision OCR may split one physical line into the attacker, spell and damage.
 rows=[]
 for item in sorted(items,key=lambda i:-(i.get('box',[0,0,0,0])[1]+i.get('box',[0,0,0,0])[3]/2)):
  if item.get('confidence',0)<.3:continue
  if 'box' not in item:
   rows.append(dict(center=None,items=[item]));continue
  b=item['box'];center=b[1]+b[3]/2
  row=next((r for r in rows if r['center'] is not None and abs(r['center']-center)<=.035),None)
  if row is None:rows.append(dict(center=center,items=[item]))
  else:row['items'].append(item)
 return [' '.join(i['text'] for i in sorted(r['items'],key=lambda i:i.get('box',[0])[0])) for r in rows]


def normalized(text):return ''.join(unicodedata.normalize('NFKC',text).split())


def same_line(left,right):
 # Ignore punctuation and modest OCR wobble; neither is a new game event.
 clean=lambda s:''.join(c for c in normalized(s) if c.isalnum())
 a,b=clean(left),clean(right)
 return a==b or (re.findall(r'\d+',a)==re.findall(r'\d+',b)
                 and min(len(a),len(b))>=8 and SequenceMatcher(None,a,b).ratio()>=.8)


def appended_lines(previous,current):
 """Ordered screen-window overlap, preserving repeated occurrences of a line."""
 for n in range(min(len(previous),len(current)),0,-1):
  if all(same_line(a,b) for a,b in zip(previous[-n:],current[:n])):
   return current[n:],True
 # Incomplete OCR or old rows becoming readable again cannot prove an append.
 if any(same_line(a,b) for a in previous for b in current):return [],False
 return current,False

def evidence(lines):
 result=[]
 for text in lines:
  t=normalized(text);kind=None
  if t.startswith('你杀死了'):kind='kill_text'
  elif re.fullmatch(r'[\[【(（]?(?:你死了|你已死亡|你已经死亡)[。.!！]?[\]】)）]?',t):kind='death_text'
  elif t.startswith('你的') and ('命中' in t or '造成' in t):kind='damage_text'
  elif not t.startswith('你的') and ('命中你' in t or '对你造成' in t):kind='incoming_damage_text'
  if kind:
   row=dict(kind=kind,text=text)
   if kind=='incoming_damage_text':
    # Conservative source label only, never a unique enemy identity/count.
    match=re.match(r'^(.{1,30}?)的.+?(?:命中你|对你造成)',t)
    if match:row['attacker_hint']=match.group(1)
   result.append(row)
 return result

class CombatLogReader:
 def __init__(self,roi,emit,tab=None):
  self.tab=tab
  self.roi=roi;self.emit=emit;self.pool=ThreadPoolExecutor(max_workers=1);self.pending=None;self.last=-float('inf');self.previous=None;self.previous_lines=[];self.initial=True;self.generation=0
  self.lock=threading.Lock();self.recent=[]
 def submit(self,frame,sequence,captured_at=None):
  if self.tab:
   from chat_tabs import selected
   if not selected(frame,self.tab):
    with self.lock:
     self.initial=True;self.previous=None;self.previous_lines=[];self.recent=[];self.generation+=1
    return
  if self.pending and not self.pending.done():return
  now=time.monotonic()
  if now-self.last<1:return
  self.last=now;x,y,w,h=self.roi;p=frame[y:y+h,x:x+w].copy()
  if self.previous is not None and np.abs(p.astype(float)-self.previous.astype(float)).mean()<1:return
  self.previous=p;self.pending=self.pool.submit(self.process,p,sequence,now if captured_at is None else captured_at,self.generation)
 def process(self,p,sequence,captured_at=None,generation=None):
  captured_at=time.monotonic() if captured_at is None else captured_at
  try:
   lines=read_lines(p)
   with self.lock:
    if generation is not None and generation!=self.generation:return
    baseline=self.initial
    added,aligned=appended_lines(self.previous_lines,lines) if not baseline and lines else ([],False)
    new=[dict(e,frame=sequence,captured_at=captured_at,transition_confirmed=aligned) for e in evidence(added)]
    # OCR wobble can break the ordered overlap while an actually new personal
    # spell line appears beneath old kill lines. Expose only novel damage text
    # as unanchored evidence; the policy still requires live HP loss and combat.
    if not baseline and not aligned and not added and lines:
     novel=[line for line in lines if not any(same_line(line,old) for old in self.previous_lines)]
     new.extend(dict(e,frame=sequence,captured_at=captured_at,transition_confirmed=False)
                for e in evidence(novel) if e['kind']=='damage_text')
    # Blank OCR is not evidence that an old line disappeared.
    if lines:self.initial=False;self.previous_lines=lines
    if not baseline:self.recent=(self.recent+new)[-40:]
   self.emit('combat_log_ocr',frame=sequence,captured_at=captured_at,baseline=baseline,lines=lines,new_evidence=[] if baseline else new,auxiliary_only=True)
  except Exception as e:self.emit('combat_log_ocr_error',error=type(e).__name__)
 def latest(self,now,since,names,include_unanchored_damage=False):
  with self.lock:rows=list(self.recent)
  return [e for e in rows if since<=e['captured_at']<=now and now-e['captured_at']<=3
          and (e['kind'] in ('death_text','incoming_damage_text') or e.get('transition_confirmed',True)
               or include_unanchored_damage and e['kind']=='damage_text')
          and (e['kind'] in ('death_text','incoming_damage_text') or any(normalized(n) in normalized(e['text']) for n in names))]
 def close(self):self.pool.shutdown(wait=True,cancel_futures=True)

def loss_decision(rows,healthy,in_combat,elapsed):
 """Evidence can extend observation, never select/attack a replacement target."""
 kinds={r['kind'] for r in rows}
 if 'death_text' in kinds:return 'combat_log_death_review'
 if not healthy:return None
 if 'kill_text' in kinds:
  return 'wait_for_kill_confirmation' if elapsed<4 else 'combat_log_kill_unconfirmed'
 if in_combat and 'damage_text' in kinds:
  return 'wait_for_target_reacquisition' if elapsed<4 else 'combat_log_target_unresolved'
 return None
