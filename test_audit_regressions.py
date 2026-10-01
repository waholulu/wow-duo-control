"""Historical reference regressions; unified runtime has separate integration tests."""
import unittest

from control_policy import Policy
from vision_state import Observation


def obs(**kw):
    values = dict(valid=True, reason='ok', player_hp=1, player_mana=1,
                  target=True, target_allowed=True, target_hp=1, bearing=0)
    values.update(kw)
    return Observation(**values)


class EncounterTests(unittest.TestCase):
    def damaged(self):
        p = Policy()
        p.step(obs(), 0, 0)
        p.step(obs(target_hp=.7), 1, 0)
        return p

    def test_target_loss_resets_even_during_cooldown(self):
        p = self.damaged()
        p.step(obs(target=False), 1.1, 0)
        self.assertFalse(p.damaged_target)
        self.assertIsNone(p.fight_started)
        self.assertIsNone(p.last_attempt)
        self.assertEqual(p.step(obs(bearing=200), 3, 0).reason, 'face_target')

    def test_delayed_xp_separate_from_new_target_damage(self):
        p = self.damaged()
        p.step(obs(target=False), 1.1, 0)
        p.step(obs(xp_visible=True), 3, 0)
        self.assertEqual(p.xp_events, 1)
        self.assertFalse(p.damaged_target)
        p.step(obs(xp_visible=True), 4, 0)
        self.assertEqual(p.xp_events, 1)

    def test_old_damage_cannot_credit_unrelated_late_xp(self):
        p = self.damaged()
        p.step(obs(target=False), 2, 0)
        p.step(obs(target=False, xp_visible=True), 20, 0)
        self.assertEqual(p.xp_events, 0)

    def test_damage_during_rest_does_not_push_timer_into_future(self):
        p = self.damaged()
        p.step(obs(target_hp=.7, player_mana=.1, in_combat=True), 3, 0)
        p.step(obs(target_hp=.6, player_mana=.1, in_combat=True), 8, 0)
        p.step(obs(target_hp=.6, player_mana=.3, in_combat=True), 10, 0)
        self.assertLessEqual(p.last_progress, 10)
        p.step(obs(target_hp=.6, in_combat=True), 21, 0)
        self.assertEqual(p.stopped, 'no_damage_in_combat')

    def test_xp_popup_does_not_erase_current_encounter_damage(self):
        p = self.damaged()
        p.step(obs(target_hp=.7, xp_visible=True), 2, 0)
        self.assertTrue(p.damaged_target)


if __name__ == '__main__':
    unittest.main()
