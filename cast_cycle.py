"""Observe a cast attempt before repeating it; unknown cast bars stay unknown."""


class CastCycle:
    def begin_cast_effect(self,obs,now):
        self.cast_pending_at=now
        self.cast_pending_hp=obs.target_hp
        self.cast_pending_mana=obs.player_mana
        self.cast_pending_mana_exact=not obs.player_mana_lower_bound
        self.cast_seen=False
        self.cast_damage_seen=self.cast_mana_seen=False
        self.cast_effect_status='awaiting_effect'

    def check_cast_effect(self,obs,now):
        if self.cast_pending_at is None:return False
        self.cast_seen |= obs.casting_known and obs.casting
        self.cast_damage_seen |= obs.target_hp<self.cast_pending_hp-.02
        self.cast_mana_seen |= (self.cast_pending_mana_exact and not obs.player_mana_lower_bound
                               and obs.player_mana<self.cast_pending_mana-.005)
        ended=self.cast_seen and obs.casting_known and not obs.casting
        if self.cast_mana_seen and (self.cast_damage_seen or ended):
            self.cast_effect_status=('cast_end_and_resource_change' if ended else 'resource_and_target_change')
            self.last_cast_effect=(now,self.cast_effect_status)
            self.cast_pending_at=None
            return False
        if now-self.cast_pending_at>self.attack_effect_timeout:
            self.cast_effect_status='unconfirmed'
            self.last_cast_effect=(now,self.cast_effect_status)
            self.stop('cast_effect_unconfirmed')
        return True
