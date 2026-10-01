import unittest
from runtime_skills import escape
from test_support import Driver

class EscapeQuietTests(unittest.TestCase):
    def test_three_fast_peaceful_frames_do_not_end_escape(self):
        d=Driver();r=d.run(escape(d.ctx))
        self.assertEqual(r.reason,'ESCAPED');self.assertGreaterEqual(d.now,5)
        self.assertEqual(d.actions,[])
    def test_damage_with_peaceful_icon_resets_quiet_period(self):
        d=Driver();original=d.frame
        def frame():
            if d.now>=1:d.observation.player_hp=.7
            return original()
        d.frame=frame;r=d.run(escape(d.ctx))
        self.assertEqual(r.reason,'ESCAPED');self.assertGreaterEqual(d.now,6)
        self.assertTrue(d.actions)
        self.assertEqual(r.facts['hp'],.7)
    def test_final_movement_is_observed_before_limit_failure(self):
        d=Driver();original=d.frame
        def frame():
            d.observation.in_combat=len(d.actions)<26
            return original()
        d.frame=frame;r=d.run(escape(d.ctx))
        self.assertEqual(r.reason,'ESCAPED')
        self.assertEqual(len(d.actions),26)
    def test_persistent_combat_stops_after_bounded_final_observation(self):
        d=Driver();d.observation.in_combat=True
        r=d.run(escape(d.ctx))
        self.assertEqual(r.reason,'ESCAPE_LIMIT')
        self.assertEqual(len(d.actions),26)
        self.assertLess(d.now,30)
if __name__=='__main__':unittest.main()
