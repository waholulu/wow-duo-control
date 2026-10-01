"""Read-only session aggregation, preserving verified catches across restarts."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
manifest=ROOT/'runs/fishing-until-full-progress.json'
def main():
 d=json.loads(manifest.read_text());total=0;casts=0;seconds=[];latest={}
 for session in d['sessions']:
  p=ROOT/'runs'/session
  status=p/'status.json'
  if status.exists():latest=json.loads(status.read_text())
  else:latest=dict(state='initializing',bag=latest.get('bag',{}))
  for folder in sorted(p.glob('cast-*')):
   result=folder/'corrected-verification.json'
   if not result.exists():result=folder/'result.json'
   if not result.exists():continue
   r=json.loads(result.read_text());casts+=1;total+=int(r['confirmed'])
   if r['confirmed']:seconds.append(r['elapsed_seconds'])
 d.update(confirmed_total=total,finished_casts=casts,empty=latest.get('bag',{}).get('empty'),state=latest.get('state'),bag_full=latest.get('bag_full',False),mean_confirmed_seconds=round(sum(seconds)/len(seconds),2) if seconds else None)
 proof=ROOT/'runs/fishing-until-full-verification.json'
 if proof.exists():
  v=json.loads(proof.read_text())
  if v.get('completed') and v.get('session')==d['sessions'][-1]:
   d.update(state='completed',bag_full=True,completion_reason=v['reason'])
 manifest.write_text(json.dumps(d,indent=2));print(json.dumps(d))
if __name__=='__main__':main()
