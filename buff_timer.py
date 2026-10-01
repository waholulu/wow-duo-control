"""Character-scoped wall-clock timers; never survive a subsequent death review."""
import json,time,math
from pathlib import Path

def remaining(path,key,refresh,gate=None,now=None):
 now=time.time() if now is None else now
 try:
  d=json.loads(Path(path).read_text());at=d['cast_wall_time']
  if d['key']!=key or not math.isfinite(at) or not 0<=now-at<refresh:return 0
  if gate and Path(gate).exists():
   g=json.loads(Path(gate).read_text())
   if g.get('status')!='resolved' or g.get('detected_at',0)>at:return 0
  return refresh-(now-at)
 except (OSError,ValueError,KeyError,TypeError):return 0

def save(path,key,now=None):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 temp=p.with_suffix('.tmp');temp.write_text(json.dumps(dict(key=key,cast_wall_time=time.time() if now is None else now)))
 temp.replace(p)
