"""Shared capability behavior; no class names or hardware access."""


class ResourceReadiness:
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
        # Waiting for mana does not undo elapsed combat time or create damage.
        self.state = 'ACTIVE'
        self.rest_started = None
