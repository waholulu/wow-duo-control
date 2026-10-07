"""Read-only local CV watchdog; samples do not acquire the hardware input lock."""
import argparse
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import shlex
import time

from class_profiles import load_class
from run_permission import permission_issues
from vision_feed import Feed
from vision_state import Vision

ROOT = Path(__file__).resolve().parent


def advance(streak, bad):
    return streak + 1 if bad else 0


def controller_status(character):
    """Inspect real command lines; old run files alone never imply activity."""
    rows=subprocess.run(['ps','ax','-o','pid=,command='],text=True,capture_output=True,check=True).stdout.splitlines()
    found=[]
    for row in rows:
        parts=row.strip().split(None,1)
        if len(parts)!=2:continue
        try:args=shlex.split(parts[1])
        except ValueError:continue
        if not args or not Path(args[0]).name.lower().startswith('python'):continue
        if not any(Path(x).name in ('wow_control.py','runtime_main.py') for x in args[1:3]):continue
        def option(flag,default=None):
            return args[args.index(flag)+1] if flag in args and args.index(flag)+1<len(args) else default
        if option('--class','warlock')!=character or '--execute' not in args:continue
        found.append(dict(pid=int(parts[0]),task=option('--task','patrol'),output=option('--output')))
    return found


def write_json(path, value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    temp.replace(path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--class',dest='character',choices=['paladin','warlock'],default='paladin')
    p.add_argument('--interval',type=float,default=10)
    p.add_argument('--consecutive',type=int,default=2)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.interval<1 or a.consecutive<1:p.error('Invalid monitoring interval or streak')
    a.output.mkdir(parents=True,exist_ok=True)
    with (ROOT/'runs'/f'{a.character}-status-monitor.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        running=True
        def stop(*_):
            nonlocal running
            running=False
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        cfg=load_class(a.character)
        vision=Vision(ROOT/cfg['vision_profile'])
        feed=None;streak=0;sequence=0;previous_alert=False
        started=time.monotonic();deadline=started
        existing=a.output/'status.json'
        history=a.output/'samples.jsonl'
        prior=json.loads(history.read_text().splitlines()[-1]) if history.exists() and history.stat().st_size else {}
        previous_alert=bool(prior.get('alert'))
        previous_signature=prior.get('issue_signature')
        if previous_signature is None and prior.get('alert'):
            previous_signature=[bool(prior.get('perception_issue')),bool(prior.get('restrictions')),bool(prior.get('controller_running')),bool(prior.get('not_in_combat'))]
        pointer=ROOT/'runs'/f'current-{a.character}-status-monitor.json'
        write_json(pointer,dict(pid=os.getpid(),run=str(a.output.resolve()),interval_seconds=a.interval,consecutive=a.consecutive))
        while running:
            delay=deadline-time.monotonic()
            if delay>0:
                time.sleep(min(delay,.2));continue
            sequence+=1
            sample=dict(sequence=sequence,wall_time=time.time(),elapsed_seconds=time.monotonic()-started,
                        pid=os.getpid(),input_sent=False,interval_seconds=a.interval)
            try:
                if feed is None:feed=Feed()
                frame,latency=feed.frame()
                before=time.monotonic()
                observation=vision.observe(frame)
                age=latency+time.monotonic()-before
                sample.update(observation=asdict(observation),capture_age_seconds=age)
                known=observation.valid and bool(vision.profile.get('combat_roi')) and age<=.5
                sample['combat_known']=known
                sample['not_in_combat']=known and not observation.in_combat
                sample['perception_issue']=None if known else observation.reason if not observation.valid else 'combat_state_unknown_or_stale'
                if observation.reason=='player_health_unreadable_or_zero':
                    from death_review import require_review
                    require_review(ROOT,a.character,a.output,observation.reason)
            except Exception as exc:
                sample.update(combat_known=False,not_in_combat=False,perception_issue=type(exc).__name__+': '+str(exc))
                if feed is not None:
                    try:feed.close()
                    except Exception:pass
                    feed=None
            controllers=controller_status(a.character)
            sample['controllers']=controllers
            sample['controller_running']=bool(controllers)
            try:
                restrictions=permission_issues(ROOT,a.character,'patrol',dict(kills=1,max_seconds=180),required=cfg.get('run_permission_required',False))
            except Exception as exc:
                restrictions=['permission_check_failed:'+type(exc).__name__]
            sample['restrictions']=restrictions
            bad=bool(sample['not_in_combat'] or restrictions or sample['perception_issue'])
            streak=advance(streak,bad)
            alert=streak>=a.consecutive
            sample.update(bad_sample=bad,consecutive_bad=streak,alert=alert,
                          state='problem' if alert else 'pending' if bad else 'in_combat',running=True)
            signature=[bool(sample['perception_issue']),bool(restrictions),bool(controllers),bool(sample['not_in_combat'])]
            sample['issue_signature']=signature
            transition='problem' if alert and not previous_alert else 'recovered' if previous_alert and not alert else 'issue_changed' if alert and previous_alert and previous_signature is not None and signature!=previous_signature else None
            if transition:
                sample['transition']=transition
                with (a.output/'alerts.jsonl').open('a') as out:out.write(json.dumps(sample,ensure_ascii=False)+'\n')
            previous_alert=alert
            previous_signature=signature
            with (a.output/'samples.jsonl').open('a') as out:out.write(json.dumps(sample,ensure_ascii=False)+'\n')
            write_json(a.output/'status.json',sample)
            print(json.dumps(sample,ensure_ascii=False),flush=True)
            deadline+=a.interval
            if deadline<time.monotonic():deadline=time.monotonic()+a.interval
        if feed is not None:feed.close()
        write_json(a.output/'status.json',dict(running=False,pid=os.getpid(),state='stopped',wall_time=time.time(),input_sent=False))


if __name__=='__main__':main()
