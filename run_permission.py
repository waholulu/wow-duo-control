"""Compose historical death review with the current run's pause/effect evidence.

Read-only admission: this module never releases a hold or changes kill counts.
"""
import json
import math
from pathlib import Path
from death_review import COMBAT_TASKS, blocking_review


def read_object(path):
    value=json.loads(Path(path).read_text())
    if not isinstance(value,dict):raise ValueError('Expected an object')
    return value


def backpack_effect_confirmed(root, run, character, after_run):
    """Require a real closed -> opened/read -> closed input-effect transaction."""
    folder=Path(root)/run
    started=read_object(folder/'run.json')['started_wall_time']
    blocked=read_object(Path(root)/after_run/'run.json')['started_wall_time']
    if (any(type(t) not in (int,float) or not math.isfinite(t) for t in (started,blocked))
            or started<=blocked):return False
    if read_object(folder/'class-profile.json').get('class')!=character:return False
    result=read_object(folder/'result.json')
    if (result.get('status')!='completed' or result.get('reason')!='backpack_confirmed'
            or result.get('facts',{}).get('closed_verified') is not True
            or len(result.get('evidence',[]))<2):return False
    opened=False
    reads=0
    closed=False
    task=None
    frames=set()
    for line in (folder/'events.jsonl').read_text().splitlines():
        row=json.loads(line)
        if row.get('kind')=='action_acknowledged' and row.get('reason')=='open_backpack':
            opened=True;reads=0;closed=False;task=row.get('task');frames=set()
        if opened and row.get('task')==task:
            if row.get('kind')=='backpack_reading':
                reading=row.get('reading',{})
                if reading.get('valid') and reading.get('open') and row.get('frame') not in frames:
                    frames.add(row['frame']);reads+=1
                elif not reading.get('valid'):reads=0;frames=set()
            if row.get('kind')=='action_acknowledged' and row.get('reason')=='close_backpack':
                closed=reads>=3
    return opened and closed


def permission_issues(root, character, task, limits=None, required=False):
    """Every independent hold must pass. Missing/corrupt referenced data blocks."""
    if task not in COMBAT_TASKS and task!='defend':return []
    root=Path(root)
    issues=[]
    try:
        blocked=blocking_review(root,character,'combat' if task=='defend' else task,limits)
        if blocked:issues.append('death_review_required: '+blocked)
    except (OSError,ValueError,KeyError,TypeError):
        issues.append('death_review_unreadable')
    path=root/'runs'/'run-permissions'/f'{character}.json'
    if not path.exists():
        if required:issues.append('run_permission_missing: '+str(path))
        return issues
    try:
        state=read_object(path)
        if state.get('character')!=character or not state.get('sources'):
            raise ValueError('Invalid permission sources')
        for source in state['sources']:
            report=read_object(root/source)
            if report.get('resume_allowed') is not True:
                issues.append('run_resume_blocked: '+str(source))
        review=read_object(root/state['review_file'])
        review_fields=('evidence','findings','fixes','verification','resume_conclusion')
        if not all(review.get(key) for key in review_fields) or review.get('resume_allowed') is not True:
            issues.append('run_review_required: '+str(state['review_file']))
        scope=review.get('trial_scope',{})
        if (not limits or task not in scope.get('tasks',[])
                or not 0<limits['kills']<=scope.get('max_kills',0)
                or not 0<limits['max_seconds']<=scope.get('max_seconds',0)):
            issues.append('run_trial_scope_required')
        if state.get('required_effect')=='backpack_open_read_close':
            run=review.get('input_effect_run')
            if not run or not backpack_effect_confirmed(root,run,character,state['blocked_since_run']):
                issues.append('pc_input_effect_unconfirmed')
        elif state.get('required_effect') is not None:
            issues.append('unknown_required_effect')
    except (OSError,ValueError,KeyError,TypeError):
        issues.append('run_permission_unreadable: '+str(path))
    return issues


def input_effect_issues(root, character):
    """A paused PC input link also blocks new noncombat gathering clicks."""
    path=Path(root)/'runs'/'run-permissions'/f'{character}.json'
    if not path.exists():
        return []
    try:
        state=read_object(path)
        if state.get('character')!=character:
            raise ValueError('Character mismatch')
        effect=state.get('required_effect')
        if effect is None:
            return []
        if effect!='backpack_open_read_close':
            return ['unknown_required_input_effect']
        review=read_object(Path(root)/state['review_file'])
        run=review.get('input_effect_run')
        if run and backpack_effect_confirmed(root,run,character,state['blocked_since_run']):
            return []
        return ['pc_input_effect_unconfirmed']
    except (OSError,ValueError,KeyError,TypeError):
        return ['run_permission_unreadable: '+str(path)]
