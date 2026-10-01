"""Replay timed, labeled observations through the real scheduler without devices."""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import threading
import time
import cv2
from vision_state import Observation, Vision
from runtime_types import Snapshot, Result
from runtime_engine import Executor, Scheduler
from runtime_store import Store
from runtime_skills import combat, backpack, loot, roam, escape, navigate


class ReplaySource:
    def __init__(self, manifest, root, vision, store):
        self.rows=manifest['frames']
        def finite(value):
            return type(value) in (int,float) and math.isfinite(value) and value>=0
        if (not self.rows or any(not finite(row.get('at')) or not finite(row.get('age_seconds',0)) for row in self.rows)
                or any(self.rows[i]['at']>=self.rows[i+1]['at'] for i in range(len(self.rows)-1))):
            raise ValueError('Replay requires nonempty monotonically timed frames')
        self.root=Path(root)
        self.vision,self.store=vision,store
        self.latest=None
        self.minerals=[]
        self.error=None
        self.stop_event=threading.Event()
        self.worker=threading.Thread(target=self._run,name='replay',daemon=True)
        self.worker.start()

    def _run(self):
        started=time.monotonic()
        try:
            for number,row in enumerate(self.rows,1):
                if self.stop_event.wait(max(0,started+row['at']-time.monotonic())):
                    return
                frame=cv2.imread(str(self.root/row['image'])) if row.get('image') else None
                if row.get('image') and frame is None:
                    raise ValueError('Missing replay image')
                if row.get('observation') is not None:
                    obs=Observation(**row['observation'])
                elif frame is not None:
                    obs=self.vision.observe(frame)
                else:
                    raise ValueError('Replay row requires image or labeled observation')
                self.latest=Snapshot(number,started+row['at']-row.get('age_seconds',0),
                    tuple(row.get('calibration',[1,0,0])),obs,frame,
                    row.get('combat_known') is True,row.get('casting_known') is True,
                    row.get('calibration_generation',0),row.get('target_track'))
                self.minerals=row.get('minerals',[])
                self.store.emit('replayed_observation',frame=number,observation=asdict(obs),source=row.get('image'),
                    captured_at=self.latest.captured_at,calibration_generation=self.latest.calibration_generation,
                    target_track=self.latest.target_track)
                if frame is not None:self.store.frame(self.latest)
        except Exception as exc:
            self.error=exc

    def peek(self):
        if self.error:raise self.error
        return self.latest

    def close(self):
        self.stop_event.set()
        self.worker.join(timeout=2)


def replay(manifest_path, output, skill='combat'):
    manifest_path=Path(manifest_path)
    manifest=json.loads(manifest_path.read_text())
    store=Store(output,dict(mode='offline_replay',manifest=str(manifest_path),hardware_opened=False))
    source=executor=scheduler=None
    result=Result('failed','replay_startup')
    try:
        source=ReplaySource(manifest,manifest_path.parent,Vision(Path(__file__).parent/'calibration/profile.json'),store)
        executor=Executor(None,source,store.emit)
        scheduler=Scheduler(source,executor,store)
        factories={'combat':lambda c:combat(c,scheduler,manifest.get('combat_seconds',90)),
                   'backpack':backpack,'loot':lambda c:loot(c,output),
                   'skin':lambda c:loot(c,output,'skin'),'roam':roam,'escape':escape,
                   'navigate':lambda c:navigate(c,output,manifest['points'])}
        result=scheduler.run(skill,factories[skill],manifest['frames'][-1]['at']+2,skill not in ('combat','escape'))
    except Exception as exc:
        result=Result('failed','replay_exception',dict(error=str(exc)))
    finally:
        if scheduler:scheduler.close()
        if executor:executor.close()
        if source:source.close()
        store.close()
        if store.error or store.worker.is_alive():
            result=Result('failed','recording_failed',dict(error=store.error,prior=asdict(result)))
        store.result(result)
    expectations=manifest.get('expected',{})
    events=[]
    for name in ('events.previous.jsonl','events.jsonl'):
        path=Path(output)/name
        if path.exists():events.extend(json.loads(line) for line in path.read_text().splitlines())
    actions=[r['reason'] for r in events if r['kind']=='action_proposed']
    passed=bool(expectations) and not store.error and not store.worker.is_alive() and not store.event_rotations
    if 'reason' in expectations:passed=passed and expectations['reason']==result.reason
    if 'status' in expectations:passed=passed and expectations['status']==result.status
    if not set(expectations)&{'reason','status','actions','forbidden_actions'}:passed=False
    if 'actions' in expectations:passed=passed and actions==expectations['actions']
    if expectations.get('forbidden_actions'):
        passed=passed and not set(actions)&set(expectations['forbidden_actions'])
    report=dict(result=asdict(result),actions=actions,expectations_passed=passed,
                hardware_opened=False,dataset=manifest.get('dataset','unlabeled'))
    from runtime_store import atomic_json
    atomic_json(Path(output)/'replay.json',report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--skill',choices=['combat','backpack','loot','skin','roam','escape','navigate'],default='combat')
    a=p.parse_args()
    r=replay(a.input,a.output,a.skill)
    print(json.dumps(r,ensure_ascii=False))
    raise SystemExit(0 if r['expectations_passed'] else 1)
