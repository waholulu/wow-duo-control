"""Shared capability behavior; no class names or hardware access."""
from control_action import Action


class MeleeRotation:
    def melee_step(self, obs, now):
        """Approach independently of spell cooldown and the target's initial HP."""
        defer_for_defense=(self.defensive and obs.in_combat and
            (obs.player_mana<self.opener_min_mana or
             (self.opener_last_at is not None and now-self.opener_last_at<self.opener_cooldown)))
        if self.opener_key is not None and obs.target_hp>.02 and not defer_for_defense:
            due=self.opener_last_at is None or now-self.opener_last_at>=self.opener_cooldown
            if not self.opener_confirmed:
                if obs.player_mana<self.opener_min_mana:return self.stop('opener_mana_reserve')
                if obs.bearing is None:return self.stop('opener_target_not_located')
                if abs(obs.bearing)>55:
                    self.ready_at=now+.3
                    return Action(79 if obs.bearing>0 else 80, min(100,max(20,int(abs(obs.bearing)*.25))),'face_target')
                if obs.opener_in_range is None:return self.stop('opener_range_unreadable')
                if not obs.opener_in_range:
                    if self.opener_approaches>=12:return self.stop('opener_approach_limit')
                    self.opener_approaches+=1;self.ready_at=now+.4
                    return Action(26,200,'approach_target')
                if not due:return None
                self.begin_opener_effect(obs,now,'ranged_opener')
                self.opener_last_at=now;self.ready_at=now+.3
                return Action(self.opener_key,80,'ranged_opener')
            elif due and self.attack_started and self.damaged_target and obs.target_hp>.15 and obs.player_mana>=self.opener_min_mana and obs.opener_in_range is True:
                self.begin_opener_effect(obs,now,'cooldown_strike')
                self.opener_last_at=now;self.ready_at=now+1.6
                return Action(self.opener_key,80,'cooldown_strike')
        if self.interact_key is not None:
            if obs.target_hp <= .02:
                if self.attack_started and self.damaged_target and obs.player_hp>=.6:
                    if self.near_zero_since is None:self.near_zero_since=now
                    if now-self.near_zero_since<6:
                        return None  # Preserve existing auto-attack; never Tab/F8 a sliver or corpse.
                    return self.stop('near_zero_target_unresolved')
                if self.ctm_active:
                    self.ctm_active = False
                    return Action(22,20,'cancel_click_to_move')
                return None
            self.near_zero_since=None
            if not self.attack_started:
                self.attack_started = self.ctm_active = True
                self.ctm_started_at = self.last_attempt = now
                self.ready_at = now + .3
                return Action(self.interact_key,80,'interact_target')
            # One hard-target interaction starts both CTM and auto-attack.
            # Do not repeat on corpses or override the game's approach with W.
            if self.damaged_target:
                if now-self.last_progress > 10:
                    return self.stop('no_damage_in_combat')
            elif self.ctm_started_at is not None and now-self.ctm_started_at > 10:
                return self.stop('click_to_move_no_damage')
            return None
        if self.damaged_target and now-self.last_progress < 3:
            return None  # Allow a full swing before walking through the target.
        if self.damaged_target and now-self.last_progress > 10:
            return self.stop('no_damage_in_combat')
        if obs.bearing is None:
            if self.melee_scans >= 12:
                return self.stop('melee_target_not_located')
            self.melee_scans += 1
            self.ready_at = now + .3
            return Action(79,80,'melee_reacquire_target')
        self.melee_scans = 0
        if abs(obs.bearing) > 60:
            self.ready_at = now + .25
            return Action(79 if obs.bearing > 0 else 80,
                          min(100,max(20,int(abs(obs.bearing)*.25))), 'face_target')
        if not self.attack_started:
            self.attack_started = True
            self.last_attempt = now
            self.ready_at = now + .25
            return Action(self.attack_key,80,'attack')
        if self.approaches >= 24:
            return self.stop('approach_limit')
        self.approaches += 1
        self.last_attempt = now
        self.ready_at = now + .65
        return Action(26,500,'approach_target')
