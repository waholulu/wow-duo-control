"""Local, monotonic-time policy. No LLM call in the action path."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    key: int
    milliseconds: int
    reason: str


class Policy:
    def __init__(self, mana_rest_at=.5, mana_resume_at=.85, skip_unknown=False):
        if not 0 < mana_rest_at < mana_resume_at <= 1:
            raise ValueError("Require 0 < rest threshold < resume threshold <= 1")
        self.mana_rest_at = mana_rest_at
        self.mana_resume_at = mana_resume_at
        self.state = "ACTIVE"
        self.rest_started = None
        self.rest_cycles = 0
        self.last_combat_at = float("-inf")
        self.previous_player_hp = None
        self.ready_at = 0.0
        self.unknown_since = None
        self.skip_unknown = skip_unknown
        self.unknown_skips = 0
        self.fight_started = None
        self.last_hp = None
        self.last_progress = 0.0
        self.searches = 0
        self.approaches = 0
        self.skipped_targets = 0
        self.last_attempt = None
        self.stopped = None
        self.xp_events = 0
        self.previous_xp = False
        self.damaged_target = False
        self.pending_xp_until = float("-inf")

    def stop(self, reason):
        self.stopped = reason
        return None

    def mana_wait(self, obs, now):
        if obs.in_combat or (self.previous_player_hp is not None and obs.player_hp < self.previous_player_hp-.02):
            self.last_combat_at = now
        self.previous_player_hp = obs.player_hp
        busy = (now-self.last_combat_at < 1.0 or
                (obs.target and self.last_attempt is not None and now-self.last_attempt < 4))
        if self.state == 'REST_MANA' and busy:
            self.leave_rest(now)
        if self.state == 'WAIT_MANA_COMBAT' and not busy:
            self.state = 'REST_MANA'
        if self.state == 'REST_MANA':
            if obs.player_mana < self.mana_resume_at:
                if now-self.rest_started > 120:
                    self.stop('mana_recovery_timeout')
                return True
            self.leave_rest(now)
            self.rest_cycles += 1
            self.ready_at = now + .2
            return True
        if self.state == 'WAIT_MANA_COMBAT':
            if obs.player_mana < .25:
                if now-self.rest_started > 30:
                    self.stop('combat_mana_recovery_timeout')
                return True
            self.leave_rest(now)
        if busy and not obs.target and obs.player_mana < self.mana_rest_at:
            return True  # Wait for the combat indicator to clear; do not pull again.
        if not busy and obs.player_mana < self.mana_rest_at:
            self.state = 'REST_MANA'
            self.rest_started = now
            return True
        if busy and obs.player_mana < .15:
            self.state = 'WAIT_MANA_COMBAT'
            self.rest_started = now
            return True
        return False

    def leave_rest(self, now):
        if self.rest_started is not None:
            paused = now-self.rest_started
            if self.fight_started is not None:
                self.fight_started += paused
            self.last_progress += now - max(self.rest_started, self.last_progress)
        self.state = 'ACTIVE'
        self.rest_started = None

    def step(self, obs, now, frame_age):
        if self.stopped:
            return None
        if frame_age > 0.5 or frame_age < 0:
            return self.stop('stale_frame')
        if not obs.valid:
            return self.stop(obs.reason)
        if obs.player_hp < 0.4:
            return self.stop('low_health')
        if obs.xp_visible and not self.previous_xp and now <= self.pending_xp_until:
            self.xp_events += 1
            self.pending_xp_until = float("-inf")
        self.previous_xp = obs.xp_visible
        # Encounter state must reset even during cooldown or mana recovery.
        # XP evidence has its own short lifetime so a delayed popup still counts.
        if not obs.target:
            self.fight_started = None
            self.last_attempt = None
            self.approaches = 0
            self.last_hp = None
            self.damaged_target = False
        if obs.target and not obs.target_allowed:
            if self.unknown_since is None:
                self.unknown_since = now
            if now - self.unknown_since >= .6:
                if (self.skip_unknown and not obs.in_combat and self.fight_started is None
                        and obs.player_hp>=.95 and self.unknown_skips<6):
                    if now<self.ready_at:return None
                    self.unknown_skips+=1
                    self.unknown_since=None
                    self.ready_at=now+.8
                    return Action(43,80,'skip_unknown_without_attack')
                return self.stop('target_not_allowed')
            return None
        self.unknown_since = None
        if obs.target and self.last_hp is not None and obs.target_hp < self.last_hp - 0.02:
            self.last_progress = now
            self.damaged_target = True
            self.pending_xp_until = now + 15
            self.skipped_targets = 0
        self.last_hp = obs.target_hp if obs.target else None
        if self.mana_wait(obs, now):
            return None
        if now < self.ready_at or obs.casting:
            return None
        if not obs.target:
            if self.searches >= 12:
                self.searches = 0
                if not obs.in_combat:
                    return self.stop('no_targets_found')
            self.searches += 1
            self.ready_at = now + 0.7
            return Action(43 if self.searches % 2 else 79,
                          80 if self.searches % 2 else 150, 'select_or_scan')
        self.searches = 0
        if self.fight_started is None:
            self.fight_started = self.last_progress = now
        if now - self.fight_started > 45:
            return self.stop('fight_timeout')
        if not self.damaged_target and obs.target_hp > 0.95:
            if not obs.in_combat and obs.bearing is not None and abs(obs.bearing) > 90:
                self.ready_at = now + 0.4
                return Action(79 if obs.bearing > 0 else 80,
                              min(120, max(20, int(abs(obs.bearing)*0.25))), 'face_target')
            if self.last_attempt is not None and now - self.last_attempt >= 3.5:
                if self.approaches >= 10:
                    return self.stop('approach_limit')
                if obs.bearing is None:
                    if now - self.last_progress < 8:
                        return None
                    self.skipped_targets += 1
                    if self.skipped_targets > 6:
                        return self.stop('visible_target_search_exhausted')
                    self.searches = 1
                    self.ready_at = now + 0.5
                    return Action(41, 80, 'skip_unseen_target')
                self.approaches += 1
                self.last_attempt = None
                self.ready_at = now + 0.7
                return Action(26, 500, 'approach_target')
        elif now - self.last_progress > 10:
            return self.stop('no_damage_in_combat')
        # Observed cast spacing. Never issue movement while waiting for a cast.
        if self.last_attempt is not None and not self.damaged_target and obs.target_hp > 0.95:
            return None
        self.last_attempt = now
        self.ready_at = now + 2.6
        return Action(31, 80, 'attack')
