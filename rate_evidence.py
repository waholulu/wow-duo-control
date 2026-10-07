"""Read-only fixed-minute kill-rate audit from local controller evidence."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def analyze(folder, minutes=5, required_per_minute=2):
    folder=Path(folder)
    rows=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines() if line]
    result=json.loads((folder/'result.json').read_text())
    # Native Store timestamps are monotonic absolute; older audited exports
    # already use relative seconds. Never subtract the origin twice.
    meta=folder/'run.json'
    origin=json.loads(meta.read_text()).get('started_monotonic',0) if meta.exists() else 0
    if rows and origin and min(r['at'] for r in rows)>=origin:
        rows=[dict(r,**{k:r[k]-origin for k in ('at','captured_at') if k in r}) for r in rows]
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
    if any(r.get('kind')=='combat_evidence_started' for r in rows):
        # A late kill remains owned by the original combat after skill exit.
        # Pair journal records with their source XP frame, not skill end time.
        confirmed=[]
        mismatches=[]
        frames={r['frame']:r for r in xp}
        seen=set()
        finished={r['task']:r for r in rows if r.get('kind')=='skill_finished'}
        for row in rows:
            if row.get('kind')!='kill_confirmed':continue
            identity=row.get('xp_event_id')
            source=frames.get(row.get('frame'))
            task=row.get('task')
            if (identity!=f"xp:{row.get('frame')}" or identity in seen or not source
                    or task not in finished
                    or abs(row.get('captured_at',-1)-source.get('captured_at',-2))>1e-6):
                mismatches.append({'invalid_kill_evidence':identity})
                continue
            seen.add(identity)
            confirmed.append(dict(at=row['captured_at'],frame=row['frame'],task=task))
        final_ids=result.get('facts',{}).get('xp_event_ids')
        if final_ids is None or len(final_ids)!=len(set(final_ids)) or set(final_ids)!=seen:
            mismatches.append({'terminal_event_ids_mismatch':True})
        for task,row in finished.items():
            facts=row.get('result',{}).get('facts',{})
            if task.endswith(('-combat','-defend')):
                ids=facts.get('xp_event_ids',[])
                owned={f"xp:{e['frame']}" for e in confirmed if e['task']==task}
                if facts.get('xp_events',0)!=len(ids) or not set(ids)<=owned or len(ids)!=len(set(ids)):
                    mismatches.append({'task':task,'invalid_skill_event_ids':ids})
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
              and not facts.get('uncertain_transaction')
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
