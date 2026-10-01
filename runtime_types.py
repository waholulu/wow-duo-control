"""Small, hardware-free contracts shared by perception, skills and execution."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Snapshot:
    sequence: int
    captured_at: float
    calibration: tuple
    observation: Any
    frame: Any = field(repr=False, compare=False)
    combat_known: bool = True
    casting_known: bool = True
    calibration_generation: int = 0
    # Observable continuity only; this is not a unique game entity identifier.
    target_track: str | None = None
    target_label: str | None = None


@dataclass(frozen=True)
class Event:
    kind: str
    at: float
    source: str
    identity: str
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Intent:
    kind: str
    values: tuple
    reason: str
    snapshot: Snapshot
    task: str
    epoch: int
    expires_at: float
    minimum_hp: float = 0.0
    peaceful: bool = False
    required_target: str | None = None
    admission_guard: Callable | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class Result:
    status: str
    reason: str
    facts: dict = field(default_factory=dict)
    evidence: tuple = ()

    def __post_init__(self):
        if self.status not in ('completed', 'skipped', 'failed', 'cancelled'):
            raise ValueError('Invalid skill result status')


@dataclass(frozen=True)
class Observe:
    after: int = 0


@dataclass(frozen=True)
class Wait:
    seconds: float


@dataclass(frozen=True)
class Work:
    function: Callable
    args: tuple = ()
    timeout: float = 10.0


@dataclass
class Context:
    task: str
    deadline: float
    epoch: int = 0
    phase: str = 'starting'
    vision_profile: dict = field(default_factory=dict)
    checkpoint: dict = field(default_factory=dict)
    sequence: int = 0
    clock: Callable = None
    record: Callable = None
    cancelled: bool = False
    required_target: str | None = None
    # Nonblocking access for final evidence guards; never performs recognition.
    current_snapshot: Callable | None = field(default=None, repr=False, compare=False)

    def observe(self):
        snapshot = yield Observe(self.sequence)
        self.sequence = snapshot.sequence
        return snapshot

    def act(self, snapshot, kind, values, reason, minimum_hp=0, peaceful=False,
            *, expires_at=None, admission_guard=None):
        expiry = min(self.deadline, snapshot.captured_at + .5)
        if expires_at is not None:
            expiry = min(expiry, expires_at)
        return (yield Intent(kind, tuple(values), reason, snapshot, self.task,
                             self.epoch, expiry, minimum_hp, peaceful,
                             self.required_target, admission_guard))

    def pause(self, seconds):
        yield Wait(seconds)

    def work(self, function, *args, timeout=10):
        return (yield Work(function, args, timeout))


class Cancelled(RuntimeError):
    pass


class GuardFailed(RuntimeError):
    pass
