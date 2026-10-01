import unittest
from runtime_skills import recover
from test_support import Driver

class RecoveryHealTests(unittest.TestCase):
    def driver(self):
        d=Driver();d.observation.player_hp=.7
        d.ctx.recovery_options={'verified':True,'self_key':58,'heal_key':32,'cast_seconds':2.5}
        return d
    def test_heal_selects_self_and_waits_for_actual_health(self):
        d=self.driver();original=d.frame
        def frame():
            if d.now>3:d.observation.player_hp=1
            return original()
        d.frame=frame
        r=d.run(recover(d.ctx))
        self.assertEqual(r.status,'completed')
        self.assertEqual([a.reason for a in d.actions],['select_self_for_heal','recovery_heal','clear_self_after_heal'])
    def test_no_health_increase_is_not_success(self):
        d=self.driver();r=d.run(recover(d.ctx))
        self.assertEqual(r.reason,'recovery_heal_unconfirmed')
    def test_threat_during_cast_stops(self):
        d=self.driver();original=d.frame
        def frame():
            if d.now>1:d.observation.in_combat=True
            return original()
        d.frame=frame
        self.assertEqual(d.run(recover(d.ctx)).reason,'combat_restarted_during_recovery')
    def test_full_health_does_not_cast(self):
        d=self.driver();d.observation.player_hp=1
        self.assertEqual(d.run(recover(d.ctx)).status,'completed');self.assertEqual(d.actions,[])
if __name__=='__main__':unittest.main()
