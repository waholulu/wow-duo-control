"""Local, monotonic-time policy. No LLM call in the action path."""
from control_action import Action
from resource_readiness import ResourceReadiness
from timed_effects import TimedEffects
from melee_rotation import MeleeRotation
from offensive_effects import OffensiveEffects
from cast_cycle import CastCycle


class Policy(ResourceReadiness, TimedEffects, MeleeRotation, OffensiveEffects, CastCycle):
    # Fields whose mutation represents an input decision rather than a fact
    # observed from the current frame. An input rejected before transmission
    # rolls these back while retaining XP, damage and health observations.
    ACTION_STATE_FIELDS = (
        'ready_at', 'searches', 'unknown_since', 'unknown_skips',
        'skipped_targets', 'approaches', 'last_attempt', 'attack_started',
        'precombat_sent', 'buff_cast_at', 'buff_waiting',
        'opener_last_at', 'opener_pending_at', 'opener_pending_hp',
        'opener_confirmed', 'opener_approaches', 'ctm_started_at',
        'ctm_active', 'melee_scans', 'near_zero_since', 'finish_started',
        'opener_pending_mana','opener_pending_mana_exact','opener_pending_kind',
        'opener_damage_seen','opener_mana_seen','opener_log_confirmed',
        'opener_correlated_log_seen','opener_effect_status',
        'cast_pending_at','cast_pending_hp','cast_pending_mana','cast_pending_mana_exact',
        'cast_seen','cast_damage_seen','cast_mana_seen','cast_effect_status',
    )

    def __init__(self, mana_rest_at=.5, mana_resume_at=.85, skip_unknown=False,
                 attack_key=31, attack_interval=2.6, attack_once=False,
                 precombat_key=None, precombat_wait=1.6, interact_key=None,
                 maintain_precombat_buff=False,buff_refresh_seconds=24,
                 opener_key=None,opener_cooldown=10.2,opener_min_mana=.35,
                 opener_log_names=(),opener_effect_timeout=3.0,attack_effect_timeout=6.0):
        if not 0 < mana_rest_at < mana_resume_at <= 1:
            raise ValueError("Require 0 < rest threshold < resume threshold <= 1")
        self.mana_rest_at = mana_rest_at
        self.mana_resume_at = mana_resume_at
        self.attack_key = attack_key
        self.attack_interval = attack_interval
        self.attack_effect_timeout=attack_effect_timeout
        self.cast_pending_at=self.cast_pending_hp=self.cast_pending_mana=None
        self.cast_pending_mana_exact=False
        self.cast_seen=self.cast_damage_seen=self.cast_mana_seen=False
        self.cast_effect_status=None
        self.last_cast_effect=self.last_opener_effect=None
        self.attack_once = attack_once
        self.precombat_key = precombat_key
        self.precombat_wait = precombat_wait
        self.maintain_precombat_buff=maintain_precombat_buff
        self.buff_refresh_seconds=buff_refresh_seconds
        self.buff_cast_at=None
        self.buff_waiting=False
        self.opener_key=opener_key
        self.opener_cooldown=opener_cooldown
        self.opener_min_mana=opener_min_mana
        self.opener_log_names=tuple(opener_log_names)
        self.opener_effect_timeout=opener_effect_timeout
        self.opener_pending_mana=None
        self.opener_pending_mana_exact=False
        self.opener_pending_kind=None
        self.opener_damage_seen=self.opener_mana_seen=self.opener_log_confirmed=False
        self.opener_correlated_log_seen=False
        self.opener_effect_status=None
        self.defensive=False
        self.opener_last_at=None
        self.opener_pending_at=None
        self.opener_pending_hp=None
        self.opener_confirmed=False
        self.opener_approaches=0
        self.interact_key = interact_key
        self.ctm_started_at = None
        self.ctm_active = False
        self.attack_started = False
        self.precombat_sent = False
        self.missing_target_since = None
        self.melee_scans = 0
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
        self.finish_started = None
        self.near_zero_since=None

    def action_checkpoint(self):
        """Capture only state coupled to a proposed input."""
        return {name:getattr(self,name) for name in self.ACTION_STATE_FIELDS}

    def reject_action(self, checkpoint):
        """Undo an unsent decision without discarding newly observed facts."""
        for name in self.ACTION_STATE_FIELDS:
            setattr(self,name,checkpoint[name])

    def reset_for_target_change(self, now):
        """Start a fresh encounter while preserving run-wide counters/timers."""
        self.opener_confirmed=False
        self.opener_pending_at=None
        self.opener_pending_hp=None
        self.opener_approaches=0
        self.cast_pending_at=None
        self.near_zero_since=None
        self.last_hp=None
        # A transient target/name loss must not toggle an active melee attack.
        if not self.attack_once:
            self.attack_started=False
            self.precombat_sent=False
        self.damaged_target=False
        self.last_attempt=None
        self.fight_started=None
        self.approaches=0
        self.last_progress=now

    def can_finish_melee(self, obs, now):
        """One bounded swing window; never relax the hard health floor."""
        eligible = (self.attack_once and self.attack_started and obs.target
                    and obs.target_allowed and .02 < obs.target_hp <= .2
                    and obs.player_hp >= .4 and self.damaged_target
                    and now-self.last_progress <= 4 and not self.stopped)
        if not eligible:
            return False
        if self.finish_started is None:
            self.finish_started = now
        return now-self.finish_started < 4

    def stop(self, reason):
        self.stopped = reason
        return None

    def step(self, obs, now, frame_age):
        if self.stopped:
            return None
        if frame_age > 0.5 or frame_age < 0:
            return self.stop('stale_frame')
        if not obs.valid:
            return self.stop(obs.reason)
        if obs.player_hp < 0.4:
            return self.stop('low_health')
        took_damage=(self.previous_player_hp is not None and obs.player_hp<self.previous_player_hp-.02)
        if obs.in_combat or took_damage:self.last_combat_at=now
        self.previous_player_hp=obs.player_hp
        if obs.xp_visible and not self.previous_xp and now <= self.pending_xp_until:
            self.xp_events += 1
            self.pending_xp_until = float("-inf")
        self.previous_xp = obs.xp_visible
        # Encounter state must reset even during cooldown or mana recovery.
        # XP evidence has its own short lifetime so a delayed popup still counts.
        if not obs.target:
            if self.attack_once:
                if self.missing_target_since is None:
                    self.missing_target_since = now
                grace = 1.2 if self.attack_started and obs.in_combat and obs.player_hp>=.85 else .6
                if now-self.missing_target_since < grace:
                    return None
            else:
                self.attack_started = False
                self.precombat_sent = False
            if self.ctm_active:
                self.ctm_active = False
                return Action(22,20,'cancel_click_to_move')
            if obs.in_combat or took_damage or now-self.last_combat_at<1:
                return self.stop('target_lost_under_threat')
            self.fight_started = None
            self.cast_pending_at=None
            self.last_attempt = None
            self.approaches = 0
            self.last_hp = None
            self.damaged_target = False
        else:
            self.missing_target_since = None
        if obs.target and not obs.target_allowed:
            if self.unknown_since is None:
                self.unknown_since = now
            if now - self.unknown_since >= .6:
                if (self.skip_unknown and not self.ctm_active and not obs.in_combat and now-self.last_combat_at>=1 and self.fight_started is None
                        and obs.player_hp>=.95 and self.unknown_skips<6):
                    if now<self.ready_at:return None
                    self.unknown_skips+=1
                    self.attack_started = self.precombat_sent = False
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
        # Resource/cast waits must not suspend encounter safety clocks.
        if obs.in_combat and obs.target and self.fight_started is None:
            self.fight_started=now
        if self.fight_started is not None:
            if now-self.fight_started>45:
                return self.stop('fight_timeout')
            if self.damaged_target and now-self.last_progress>10:
                return self.stop('no_damage_in_combat')
            if (self.ctm_started_at is not None and not self.damaged_target
                    and now-self.ctm_started_at>10):
                return self.stop('click_to_move_no_damage')
        if obs.target and self.check_opener_effect(obs,now):return None
        if obs.target and not self.attack_once and self.check_cast_effect(obs,now):return None
        # Normal pulls wait before approaching. An interrupted skill may defend
        # only the scheduler's already-associated target (never a new Tab pull).
        if (self.opener_key is not None and not self.opener_confirmed and not self.attack_started
                and self.opener_last_at is not None and now-self.opener_last_at<self.opener_cooldown
                and not self.defensive):
            if obs.in_combat:return self.stop('opener_cooldown_under_threat')
            return None
        if self.maintain_precombat_buff and obs.target and obs.target_hp>.02:
            action=self.maintain_buff(obs,now)
            if action or self.buff_waiting or self.stopped:return action
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
            self.attack_started = self.precombat_sent = False
            self.ready_at = now + 0.7
            return Action(43 if self.searches % 2 else 79,
                          80 if self.searches % 2 else 150, 'select_or_scan')
        self.searches = 0
        if not self.maintain_precombat_buff and self.precombat_key is not None and not self.precombat_sent and not obs.in_combat:
            self.precombat_sent = True
            self.ready_at = now + self.precombat_wait
            return Action(self.precombat_key, 80, 'precombat_buff')
        if self.fight_started is None:
            self.fight_started = self.last_progress = now
        if now - self.fight_started > 45:
            return self.stop('fight_timeout')
        if self.attack_once:
            return self.melee_step(obs, now)
        if not self.damaged_target and obs.target_hp > 0.95:
            if self.last_cast_effect is not None and self.last_cast_effect[0]>=self.fight_started:
                # A completed cast with resource spend may still be in flight.
                # Full target HP is not evidence that walking forward is safe.
                if now-self.last_progress>10:return self.stop('no_damage_in_combat')
                return None
            if (not obs.in_combat or self.attack_once) and obs.bearing is not None and abs(obs.bearing) > 90:
                self.ready_at = now + 0.4
                return Action(79 if obs.bearing > 0 else 80,
                              min(120, max(20, int(abs(obs.bearing)*0.25))), 'face_target')
            if self.last_attempt is not None and now - self.last_attempt >= (1.0 if self.attack_once else 3.5):
                if self.approaches >= 10:
                    return self.stop('approach_limit')
                if obs.bearing is None:
                    if now - self.last_progress < 8:
                        return None
                    if (obs.in_combat or now-self.last_combat_at<1
                            or obs.player_hp<.95):
                        return self.stop('unseen_target_under_threat')
                    self.skipped_targets += 1
                    if self.skipped_targets > 6:
                        return self.stop('visible_target_search_exhausted')
                    self.searches = 1
                    self.attack_started = self.precombat_sent = False
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
        self.ready_at = now + self.attack_interval
        if self.attack_once and self.attack_started:
            return None
        self.attack_started = True
        if not self.attack_once:self.begin_cast_effect(obs,now)
        return Action(self.attack_key, 80, 'attack')
