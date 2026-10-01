"""Shared capability behavior; no class names or hardware access."""
from control_action import Action


class TimedEffects:
    def maintain_buff(self,obs,now):
        self.buff_waiting=True
        if self.buff_cast_at is not None and now-self.buff_cast_at<self.precombat_wait:
            return None
        due=self.buff_cast_at is None or now-self.buff_cast_at>=self.buff_refresh_seconds
        if not due:
            self.precombat_sent=True
            self.buff_waiting=False
            return None
        if obs.player_mana<.15:return self.stop('required_buff_insufficient_mana')
        if obs.casting or now<self.ready_at:return None
        self.buff_cast_at=now
        self.ready_at=now+self.precombat_wait
        return Action(self.precombat_key,80,'maintain_required_buff')
