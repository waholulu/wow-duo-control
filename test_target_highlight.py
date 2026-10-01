import unittest
from pathlib import Path
import cv2
from vision_state import Vision

class HighlightTests(unittest.TestCase):
    def test_current_layout_sturdy_rockjaw_has_health_and_allowed_name(self):
        p=Path('tests/fixtures/patrol-0928-01/cycle-1/kill/000073.jpg')
        if not p.exists():self.fail('Replay absent')
        obs=Vision('calibration/profile.json').observe(cv2.imread(str(p)))
        self.assertTrue(obs.valid and obs.target and obs.target_allowed)
        self.assertGreater(obs.target_hp,.95)
        from bag_probe import BagVision
        bag=BagVision().observe(cv2.imread(str(p)))
        self.assertTrue(bag['open']);self.assertTrue(bag['valid'])
    def test_confirmed_rockjaw_highlight_is_allowed(self):
        p=Path('tests/fixtures/calibration-combat-exception.jpg')
        if not p.exists():self.fail('Replay absent')
        self.assertTrue(Vision('calibration/profile.json').allowed_target(cv2.imread(str(p)),True))
    def test_friendly_spirit_healer_is_not_allowed(self):
        for name in ('healer-dialog.jpg','healer-dialog-2.jpg','healer-dialog-3.jpg'):
            p=Path('tests/fixtures')/name
            if not p.exists():self.fail('Replay absent')
            with self.subTest(name=name):
                self.assertFalse(Vision('calibration/profile.json').allowed_target(cv2.imread(str(p)),True))

if __name__=='__main__':unittest.main()
