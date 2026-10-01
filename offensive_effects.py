"""Bounded spell effect checks; transport acknowledgement is not game success."""
from combat_log_cv import normalized


class OffensiveEffects:
    def begin_opener_effect(self,obs,now,kind):
        self.opener_pending_at=now
        self.opener_pending_hp=obs.target_hp
        self.opener_pending_mana=obs.player_mana
        self.opener_pending_mana_exact=not obs.player_mana_lower_bound
        self.opener_pending_kind=kind
        self.opener_damage_seen=False
        self.opener_mana_seen=False
        self.opener_log_confirmed=False
        self.opener_effect_status='awaiting_effect'

    def observe_offensive_log(self,rows,now):
        if self.opener_pending_at is None:return
        self.opener_log_confirmed=self.opener_log_confirmed or any(
            row.get('kind')=='damage_text'
            and self.opener_pending_at<=row.get('captured_at',-1)<=now
            and now-row['captured_at']<=3
            and row.get('transition_confirmed',True)
            and any(normalized(name) in normalized(row.get('text','')) for name in self.opener_log_names)
            for row in rows)

    def check_opener_effect(self,obs,now):
        if self.opener_pending_at is None:return False
        self.opener_damage_seen |= obs.target_hp<self.opener_pending_hp-.02
        self.opener_mana_seen |= (self.opener_pending_mana_exact and not obs.player_mana_lower_bound
                                 and obs.player_mana<self.opener_pending_mana-.005)
        if self.opener_log_confirmed or (self.opener_damage_seen and self.opener_mana_seen):
            self.opener_effect_status=('personal_spell_log' if self.opener_log_confirmed
                                      else 'resource_and_target_change')
            self.last_opener_effect=(now,self.opener_pending_kind,self.opener_effect_status)
            self.opener_confirmed=True
            self.opener_pending_at=None
            return False
        if now-self.opener_pending_at>self.opener_effect_timeout:
            self.opener_effect_status='unconfirmed'
            self.last_opener_effect=(now,self.opener_pending_kind,self.opener_effect_status)
            self.stop('opener_not_confirmed' if self.opener_pending_kind=='ranged_opener'
                      else 'cooldown_strike_not_confirmed')
        return True
