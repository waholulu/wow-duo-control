"""Persistent investigation gate; zero HP is suspicion, never a death diagnosis."""
import json,time
from pathlib import Path

COMBAT_TASKS={'patrol','hunt','combat','roam'}

def gate_path(root,character):
    return Path(root)/'runs'/'death-review-gates'/f'{character}.json'

def require_review(root,character,run,reason,confirmed=False):
    path=gate_path(root,character);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and json.loads(path.read_text()).get('status')=='review_required':return
    record=dict(status='review_required',character=character,run=str(Path(run).resolve()),
                detected_at=time.time(),reason=reason,death_confirmed=confirmed,
                required=['death_or_occlusion_evidence','timeline_and_target_identity','buff_and_input_effects',
                          'damage_and_additional_threats','escape_and_recovery','root_cause_or_unknowns',
                          'fixes_and_verification','explicit_resume_conclusion'])
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
    folder=Path(run)
    if folder.is_dir():(folder/'death-review-required.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')

def write_review_draft(root,character,run):
    gate=gate_path(root,character)
    if not gate.exists():return
    record=json.loads(gate.read_text())
    if record.get('run')!=str(Path(run).resolve()):return
    report=dict(status='review_required',resume_allowed=False,trigger=record,
                log_summary=summarize_events(run),
                findings=[],fixes=[],verification=[],resume_conclusion='Pending evidence-based investigation; resurrection does not clear this gate.')
    (Path(run)/'death-review-draft.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')

def blocking_review(root,character,task,limits=None):
    path=gate_path(root,character)
    if task not in COMBAT_TASKS or not path.exists():return None
    record=json.loads(path.read_text())
    if record.get('status')!='resolved':return str(path)
    # A resolved label alone is insufficient to authorize another combat trial.
    report=Path(record.get('review_file',''))
    if not report.is_file():return str(path)
    review=json.loads(report.read_text())
    required=('evidence','findings','fixes','verification','resume_conclusion')
    if not all(review.get(key) for key in required) or review.get('resume_allowed') is not True:return str(path)
    scope=review.get('trial_scope')
    if scope:
        if task not in scope['tasks'] or not limits:return str(path)
        if not 0<limits['kills']<=scope['max_kills'] or not 0<limits['max_seconds']<=scope['max_seconds']:return str(path)
    return None

def summarize_events(run):
    path=Path(run)/'events.jsonl';result=dict(observations=0,minimum_valid_hp=None,invalid_reasons={},actions=[],outcomes=[])
    if not path.exists():return result
    for line in path.read_text().splitlines():
        try:r=json.loads(line)
        except ValueError:continue
        if r.get('kind')=='observation':
            o=r['observation'];result['observations']+=1
            if o['valid']:
                hp=o['player_hp'];result['minimum_valid_hp']=hp if result['minimum_valid_hp'] is None else min(hp,result['minimum_valid_hp'])
            else:
                why=o.get('reason','unknown');result['invalid_reasons'][why]=result['invalid_reasons'].get(why,0)+1
        if r.get('kind')=='action_acknowledged':result['actions'].append({k:r.get(k) for k in ('at','task','reason','frame')})
        if r.get('kind')=='skill_finished':result['outcomes'].append({k:r[k] for k in ('at','task','result')})
    return result
