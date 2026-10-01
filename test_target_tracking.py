import unittest
from control_policy import Policy
from vision_state import Observation


class TargetTrackingTests(unittest.TestCase):
    def test_unknown_target_never_receives_input(self):
        p = Policy()
        obs = Observation(valid=True, reason='ok', player_hp=1, player_mana=1,
                          target=True, target_hp=1, target_allowed=False)
        for t in [0, .2, .4]:
            self.assertIsNone(p.step(obs, t, 0))
            self.assertIsNone(p.stopped)
        self.assertIsNone(p.step(obs, .61, 0))
        self.assertEqual(p.stopped, 'target_not_allowed')

    def test_single_bad_name_frame_recovers_without_attack_during_uncertainty(self):
        p = Policy()
        obs = Observation(valid=True, reason='ok', player_hp=1, player_mana=1,
                          target=True, target_hp=.7, target_allowed=False)
        self.assertIsNone(p.step(obs, 0, 0))
        obs.target_allowed = True
        self.assertEqual(p.step(obs, .2, 0).reason, 'attack')
        self.assertIsNone(p.stopped)


if __name__ == '__main__':
    unittest.main()
