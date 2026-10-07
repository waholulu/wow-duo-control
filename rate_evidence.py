"""Read-only fixed-minute kill-rate audit from local controller evidence."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def analyze(folder, minutes=5, required_per_minute=2):
    folder=Path(folder)
    rows=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines() if line]
    result=json.loads((folder/'result.json').read_text())
    starts={row['task']:row['at'] for row in rows
            if row.get('kind')=='skill_started' and row.get('task','').endswith('-combat')}
    xp=[row for row in rows if row.get('kind')=='observation'
        and row.get('observation',{}).get('xp_visible')]
    confirmed=[]
    mismatches=[]
    for row in rows:
        task=row.get('task','')
        if row.get('kind')!='skill_finished' or task not in starts:continue
        count=row.get('result',{}).get('facts',{}).get('xp_events',0)
        candidates=[event for event in xp if starts[task]<=event['at']<=row['at']]
        if len(candidates)!=count:
            mismatches.append({'task':task,'controller':count,'visual_events':len(candidates)})
        confirmed.extend({'at':event['at'],'frame':event['frame'],'task':task}
                         for event in candidates[:count])
    confirmed.sort(key=lambda event:event['at'])
    stages=defaultdict(lambda:{'count':0,'seconds':0.0})
    for row in rows:
        if row.get('kind')!='skill_finished':continue
        task=row.get('task','')
        stage=task.split('-',1)[1] if '-' in task else task
        reason=row.get('result',{}).get('reason','unknown')
        key=f'{stage}:{reason}'
        stages[key]['count']+=1
        stages[key]['seconds']+=row.get('elapsed_seconds',0)
    stage_breakdown={key:{'count':value['count'],
                          'seconds':round(value['seconds'],2)}
                     for key,value in sorted(stages.items())}
    buckets=[sum(60*i<=event['at']<60*(i+1) for event in confirmed)
             for i in range(minutes)]
    duration=max((row['at'] for row in rows),default=0)
    controller_count=result.get('facts',{}).get('confirmed_kills')
    if controller_count is not None and controller_count!=len(confirmed):
        mismatches.append({'controller_total':controller_count,'paired_events':len(confirmed)})
    status=result.get('status')
    reason=result.get('reason')
    facts=result.get('facts',{})
    # A timed patrol normally ends as cancelled/run_deadline. Accept that
    # planned boundary only when terminal safety monitoring confirmed peace.
    acceptable_end=(status=='completed' or
                    (status=='cancelled' and reason=='run_deadline'))
    safety_confirmed=facts.get('safety_exit')=='peace_confirmed'
    rate_met=(duration>=minutes*60 and all(n>=required_per_minute for n in buckets)
              and not mismatches and acceptable_end and safety_confirmed
              and not facts.get('review_required')
              and not facts.get('manual_attention_required'))
    return {'schema_version':1,'run':folder.name,'window_seconds':60,
            'minutes':minutes,'required_per_minute':required_per_minute,
            'duration_seconds':duration,'confirmed_kills':confirmed,
            'stage_breakdown':stage_breakdown,
            'kills_per_minute':buckets,'mismatches':mismatches,'rate_met':rate_met,
            'run_status':status,'run_reason':reason,
            'acceptable_end':acceptable_end,'safety_confirmed':safety_confirmed,
            'safety_exit':facts.get('safety_exit')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args()
    print(json.dumps(analyze(args.run),ensure_ascii=False,indent=2))
