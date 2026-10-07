"""Session kill evidence, independent of skill lifetime and latest-frame delivery.

No input authorization. An acknowledged attack, same-track damage and a fresh
XP edge are required. Unknown/ambiguous events remain uncounted.
"""
from collections import deque
from dataclasses import replace
import threading

from runtime_types import GuardFailed


class CombatEvidence:
    def __init__(self, record, capacity=4096):
        self.record = record
        self.capacity = capacity
        self.lock = threading.RLock()
        self.history = deque(maxlen=64)
        self.encounter = None
        self.confirmed = []
        self.credited_tracks = set()
        self.last_sequence = 0
        self.previous_xp = False
        self.error = None

    def observe(self, snapshot):
        with self.lock:
            if snapshot.sequence <= self.last_sequence:
                return
            self.last_sequence = snapshot.sequence
            # Keep only small immutable copies, never retained video frames.
            s = replace(snapshot, frame=None, observation=replace(snapshot.observation))
            edge = bool(s.observation.valid and s.observation.xp_visible and not self.previous_xp)
            self.previous_xp = bool(s.observation.xp_visible)
            self.history.append((s, edge))
            self._process(s, edge)

    def acknowledge_attack(self, snapshot, task, sent_at):
        with self.lock:
            o = snapshot.observation
            if not (snapshot.target_track and o.valid and o.target and o.target_allowed):
                return
            if snapshot.target_track in self.credited_tracks:
                return
            key = (task, snapshot.target_track)
            if self.encounter and self.encounter['key'] == key:
                return  # Repeated strikes must not reset damage or count again.
            self.encounter = dict(key=key, task=task, track=snapshot.target_track,
                                  calibration=(snapshot.calibration, snapshot.calibration_generation),
                                  sent_at=sent_at, hp=o.target_hp, damage_at=None, done=False)
            # The game can react while the serial ACK is still in flight.
            for s, edge in self.history:
                if s.captured_at >= sent_at:
                    self._process(s, edge)

    def _process(self, s, edge):
        e = self.encounter
        if not e or e['done'] or s.captured_at < e['sent_at']:
            return
        o = s.observation
        if ((s.calibration, s.calibration_generation) != e['calibration'] or
                (s.target_track is not None and s.target_track != e['track'])):
            e['done'] = True
            return
        if not o.valid:
            # Unreadable resources alone do not establish a new entity.
            if o.reason != 'status_text_unreadable':
                e['done'] = True
            return
        if o.target and not o.target_allowed:
            e['done'] = True
            return
        if s.target_track == e['track'] and o.target and o.target_allowed:
            if o.target_hp < e['hp'] - .02:
                e['damage_at'] = s.captured_at
            e['hp'] = o.target_hp
        if not edge or e['damage_at'] is None or not 0 <= s.captured_at-e['damage_at'] <= 15:
            return
        if len(self.confirmed) >= self.capacity:
            self.error = 'combat_evidence_capacity_exceeded'
            raise GuardFailed(self.error)
        event = dict(xp_event_id=f'xp:{s.sequence}', frame=s.sequence,
                     captured_at=s.captured_at, task=e['task'], target_track=e['track'])
        self.confirmed.append(event)
        self.credited_tracks.add(e['track'])
        e['done'] = True
        self.record('kill_confirmed', **event)

    def facts(self, task=None):
        with self.lock:
            events = [dict(e) for e in self.confirmed if task is None or e['task'] == task]
            return dict(xp_events=len(events), xp_event_ids=[e['xp_event_id'] for e in events])
