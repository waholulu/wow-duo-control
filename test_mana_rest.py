import unittest
from control_policy import Policy
from vision_state import Observation


def observation(mana, **kwargs):
    values=dict(valid=True, reason='ok', player_hp=1, player_mana=mana)
    values.update(kwargs)
    return Observation(**values)


class ManaRestTests(unittest.TestCase):
    def test_rest_is_stationary_and_resumes_after_hysteresis(self):
        p=Policy()
        for now,mana in [(0,.08),(3,.3),(8,.51),(12,.84)]:
            self.assertIsNone(p.step(observation(mana),now,0))
            self.assertEqual(p.state,'REST_MANA')
            self.assertIsNone(p.stopped)
        self.assertIsNone(p.step(observation(.86),15,0))
        self.assertEqual(p.rest_cycles,1)
        self.assertEqual(p.step(observation(.86),15.3,0).key,43)

    def test_selected_unpulled_target_does_not_trigger_attack(self):
        p=Policy()
        obs=observation(.2,target=True,target_allowed=True,target_hp=1,bearing=0)
        self.assertIsNone(p.step(obs,0,0));self.assertEqual(p.state,'REST_MANA')

    def test_combat_recovers_only_enough_mana_to_continue(self):
        p=Policy()
        obs=observation(.1,target=True,target_allowed=True,target_hp=.5,in_combat=True)
        self.assertIsNone(p.step(obs,0,0));self.assertEqual(p.state,'WAIT_MANA_COMBAT')
        obs.player_mana=.2
        self.assertIsNone(p.step(obs,4,0))
        obs.player_mana=.26
        self.assertEqual(p.step(obs,6,0).reason,'attack')

    def test_attack_during_rest_exits_passive_rest(self):
        p=Policy();p.step(observation(.4),0,0)
        obs=observation(.4,target=True,target_allowed=True,target_hp=.5,in_combat=True)
        self.assertEqual(p.step(obs,2,0).reason,'attack')
        self.assertEqual(p.state,'ACTIVE')

    def test_bad_frame_and_low_health_still_stop(self):
        for obs,age,reason in [(observation(.1),1,'stale_frame'),
                               (observation(.1,player_hp=.3),0,'low_health')]:
            p=Policy();self.assertIsNone(p.step(obs,0,age));self.assertEqual(p.stopped,reason)

    def test_rest_timeout_and_no_pull_during_combat_end(self):
        p=Policy();p.step(observation(.1),0,0);p.step(observation(.1),121,0)
        self.assertEqual(p.stopped,'mana_recovery_timeout')
        p=Policy();self.assertIsNone(p.step(observation(.3,in_combat=True),0,0))


if __name__=='__main__':unittest.main()
