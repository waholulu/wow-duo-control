"""Shared image-derived threat evidence. No entity counts or input authority from OCR."""
from collections import deque
from dataclasses import dataclass, asdict, replace
import math


@dataclass(frozen=True)
class ThreatState:
    fresh: bool = False
    visual: bool = False
    damaged: bool = False
    incoming: bool = False
    sources: tuple = ()
    additional_suspected: bool = False
    episode_drop: float = 0.0
    quiet_seconds: float = 0.0
    quiet_frames: int = 0

    @property
    def active(self):
        return self.visual or self.incoming

    def peaceful(self, seconds=3):
        return self.fresh and not self.active and self.quiet_frames >= 3 and self.quiet_seconds >= seconds


class ThreatMonitor:
    def __init__(self, damage_window_seconds=2.0, damage_drop=.02):
        if not (math.isfinite(damage_window_seconds) and .5 <= damage_window_seconds <= 5
                and math.isfinite(damage_drop) and .005 <= damage_drop <= .1):
            raise ValueError('invalid_threat_settings')
        self.window, self.drop = damage_window_seconds, damage_drop
        self.samples = deque()
        self.last_sequence = None
        self.last_capture = None
        self.state = ThreatState()
        self.episode_peak = None
        self.quiet_since = None
        self.quiet_frames = 0

    def update(self, snapshot, now, rows=()):
        o = snapshot.observation
        if (not o.valid or not snapshot.combat_known or not 0 <= now-snapshot.captured_at <= .5
                or not math.isfinite(o.player_hp) or not 0 < o.player_hp <= 1):
            self.quiet_since = None
            self.quiet_frames = 0
            self.samples.clear()
            self.state = replace(self.state, fresh=False, quiet_seconds=0, quiet_frames=0)
            return self.state
        # Polling does not create health samples or advance peace. OCR may finish
        # between polls, so fresh incoming evidence is still merged below.
        new = snapshot.sequence != self.last_sequence
        damaged = self.state.damaged if not new else False
        if new:
            if self.last_capture is not None and not 0 < snapshot.captured_at-self.last_capture <= self.window:
                self.samples.clear()
                self.quiet_since = None
                self.quiet_frames = 0
            while self.samples and snapshot.captured_at-self.samples[0][0] > self.window:
                self.samples.popleft()
            if self.samples:
                falling = o.player_hp < self.samples[-1][1] - 1e-6
                damaged = falling and max(hp for _, hp in self.samples)-o.player_hp > self.drop+1e-6
            self.samples.append((snapshot.captured_at, o.player_hp))
            self.last_sequence = snapshot.sequence
            self.last_capture = snapshot.captured_at
        recent = [r for r in rows if r.get('kind') == 'incoming_damage_text'
                  and 0 <= now-r.get('captured_at', -math.inf) <= 3]
        sources = tuple(sorted({r['attacker_hint'] for r in recent
                               if r.get('attacker_hint') and r.get('transition_confirmed', False)}))
        visual = bool(o.in_combat or damaged)
        active = visual or bool(recent)
        if self.episode_peak is None:
            self.episode_peak = max((hp for _, hp in self.samples), default=o.player_hp)
        self.episode_peak = max(self.episode_peak, o.player_hp)
        if active:
            self.quiet_since = None
            self.quiet_frames = 0
        elif new:
            if self.quiet_since is None:
                self.quiet_since = snapshot.captured_at
            self.quiet_frames += 1
        quiet = 0 if self.quiet_since is None else max(0, self.last_capture-self.quiet_since)
        self.state = ThreatState(True, visual, damaged, bool(recent), sources, len(sources) >= 2,
                                 max(0, self.episode_peak-o.player_hp), quiet, self.quiet_frames)
        if self.state.peaceful():
            self.episode_peak = o.player_hp
        return self.state


def observe_threat(owner, snapshot, now=None):
    """Scheduler and skill contexts share this monitor across phase changes."""
    if not hasattr(owner, 'threat_monitor'):
        owner.threat_monitor = ThreatMonitor()
    now = owner.clock() if now is None else now
    reader = getattr(owner, 'threat_log', None)
    if reader is None:
        reader = getattr(getattr(owner, 'source', None), 'combat_log', None)
    rows = reader.latest(now, now-3, []) if reader else []
    state = owner.threat_monitor.update(snapshot, now, rows)
    record = getattr(owner, 'record', None)
    # Only state transitions are recorded; frame evidence remains in perception.
    signature = (state.fresh, state.visual, state.damaged, state.incoming, state.sources)
    if record and signature != getattr(owner, '_threat_signature', None):
        record('threat_state', frame=snapshot.sequence, **asdict(state))
        owner._threat_signature = signature
    return state
