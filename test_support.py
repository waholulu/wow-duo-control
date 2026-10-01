"""Shared offline replay helpers; no TestCase classes or hardware ownership."""
from pathlib import Path
from types import SimpleNamespace
from runtime_types import Context, Observe, Wait, Work, Intent, Snapshot, GuardFailed
from vision_state import Observation

class Driver:
    """Deterministic skill replay; never creates a device or a capture source."""
    def __init__(self):
        self.now=0
        self.sequence=0
        self.actions=[]
        self.frame_image=object()
        self.observation=Observation(valid=True,player_hp=1,player_mana=1)
        self.ctx=Context('replay',100,clock=lambda:self.now,record=lambda *a,**kw:None)
        self.ctx.current_snapshot=self.frame

    def frame(self):
        self.sequence+=1
        return Snapshot(self.sequence,self.now,(1,0,0),self.observation,self.frame_image)

    def run(self,generator):
        value=None
        for _ in range(10000):
            try:op=generator.send(value)
            except StopIteration as done:return done.value
            self.now+=.001
            if isinstance(op,Observe):value=self.frame()
            elif isinstance(op,Wait):self.now+=op.seconds;value=None
            elif isinstance(op,Work):value=op.function(*op.args)
            elif isinstance(op,Intent):
                if op.admission_guard is not None and op.admission_guard() is not True:
                    raise GuardFailed('action_evidence_changed_before_send')
                self.actions.append(op);value={'sent':True}
            else:raise AssertionError(op)
            if self.now>self.ctx.deadline:raise TimeoutError('test skill exceeded deadline')
        raise AssertionError('Skill did not converge')


def immediate(value):
    if False:yield
    return value


def make_controller(flags):
    from runtime_main import Controller, parser
    args=parser().parse_args(['--execute','--output','unused',*flags])
    return Controller(args,SimpleNamespace(),SimpleNamespace(folder=Path('unused'),status=lambda **kw:None),{})


def requires_archive(*patterns):
    """Missing optional historical runs skip replay only; failed assertions do not."""
    from functools import wraps
    import unittest
    def decorate(function):
        @wraps(function)
        def checked(*args,**kwargs):
            root=Path(__file__).resolve().parent
            missing=[p for p in patterns if not any(f.is_file() for f in root.glob(p))]
            if missing:raise unittest.SkipTest('缺少历史回放资料：'+', '.join(missing))
            return function(*args,**kwargs)
        return checked
    return decorate
