"""Bounded session recovery for fishing; child remains the only input owner."""
import argparse,json,subprocess,time,sys,re
from pathlib import Path
from fishing_config import ROOT,load_config

def fish_names(lines):
 names=[]
 for line in lines:
  match=re.search(r'新鲜的([\u4e00-\u9fff]+)',line)
  if not match:return None
  names.append(match.group(1).translate(str.maketrans({'鯛':'鲷','鯰':'鲶'})))
 return set(names)

def recoverable_exit(result):
 reason=result.get('reason','');last=result.get('history',[])[-1:]
 if reason.startswith(('New own bobber not confirmed','Bobber tooltip not verified','Silver bobber ring not isolated','No interactable fishing bobber confirmed')):
  return 'pre_reel_positioning'
 if reason!='cast_limit_or_unconfirmed' or not last:return None
 cast=last[0]
 if not cast.get('reel_sent'):return 'no_reel_sent'
 bag=result.get('bag') or {}
 before=fish_names(cast.get('before',{}).get('fish',[]))
 after=fish_names(cast.get('after',{}).get('fish',[]))
 if (cast.get('confirmed') is False and cast.get('inventory_changed') is False
     and cast.get('game_inventory_full') is False and result.get('bag_full') is False
     and bag.get('valid') is True and bag.get('unknown')==0
     and before is not None and after is not None and after<=before):
  return 'verified_no_new_loot'
 return None

def main():
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--config',type=Path,default=ROOT/'calibration/fishing_until_full.json');p.add_argument('--manifest',type=Path,default=ROOT/'runs/fishing-until-full-progress.json');a=p.parse_args()
 if not a.execute:p.error('--execute required')
 c=load_config(a.config);out=ROOT/'runs'/('fishing-supervised-'+time.strftime('%Y%m%d-%H%M%S'));out.mkdir()
 print('SUPERVISOR',str(out),flush=True)
 manifest=a.manifest;data=json.loads(manifest.read_text())
 deadline=time.monotonic()+c['max_seconds'];failures=0
 for attempt in range(1,51):
  if (out/'STOP').exists() or time.monotonic()>deadline:break
  child=out/f'session-{attempt:02}';data['sessions'].append(str(child.relative_to(ROOT/'runs')));manifest.write_text(json.dumps(data,indent=2))
  proc=subprocess.Popen([sys.executable,str(ROOT/'wow_control.py'),'--task','fish-trial','--config',str(a.config.resolve()),'--execute','--output',str(child)])
  try:
   while proc.poll() is None:
    if (out/'STOP').exists() or time.monotonic()>deadline:
     if child.exists():(child/'STOP').touch()
    time.sleep(.2)
  except BaseException:
   if child.exists():(child/'STOP').touch()
   proc.wait(timeout=10);raise
  result=child/'result.json';s=json.loads(result.read_text()) if result.exists() else dict(state='failed',reason='missing child result')
  (out/'status.json').write_text(json.dumps(dict(attempt=attempt,child=str(child),**{k:v for k,v in s.items() if k not in ['history','bag']}),indent=2))
  if s.get('bag_full'):
   print('BAG_FULL_VERIFIED',flush=True);return 0
  if (out/'STOP').exists() or time.monotonic()>deadline:break
  reason=s.get('reason','');recovery=recoverable_exit(s)
  failures=(0 if s.get('confirmed_total',0)>0 else failures)+1
  if not recovery or failures>=3:
   print('RECOVERY_STOP',reason,flush=True);return 1
  print('LOCAL_RECOVERY',recovery,reason,flush=True)
  # Wait for any unattended cast to end, then acquire a new before/after pair.
  end=time.monotonic()+c['wait_seconds']
  while time.monotonic()<end:
   if (out/'STOP').exists() or time.monotonic()>deadline:return 1
   time.sleep(.2)
 return 1
if __name__=='__main__':raise SystemExit(main())
