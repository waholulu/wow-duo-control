import unittest
import cv2
import numpy as np
from bag_probe import BagVision,ROOT

class BagVisionTests(unittest.TestCase):
    def test_calibrated_backpack_has_six_empty_slots(self):
        result=BagVision().observe(cv2.imread(str(ROOT/'tests/fixtures'/'bag-calibration.png')))
        self.assertTrue(result['valid'])
        self.assertEqual((result['capacity'],result['empty'],result['occupied']),(20,6,14))

    def test_corpse_tooltip_obstruction_is_not_reported_as_full(self):
        result=BagVision().observe(cv2.imread(str(ROOT/'tests/fixtures'/'bag-after-loot-live-01'/'backpack.jpg')))
        self.assertFalse(result['valid'])
        self.assertGreater(result['unknown'],0)

    def test_player_tooltip_is_explicitly_rejected(self):
        result=BagVision().observe(cv2.imread(str(ROOT/'tests/fixtures'/'hunt-skin-02'/'cycle-2'/'backpack'/'backpack.jpg')))
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'],'hover_tooltip_obstruction')

    def test_green_foliage_below_bag_is_not_a_hover_card(self):
        result=BagVision().observe(cv2.imread(str(ROOT/'tests/fixtures'/'hunt-skin-05'/'cycle-1'/'backpack'/'backpack.jpg')))
        self.assertTrue(result['valid'])
        self.assertEqual(result['empty'],8)

    def test_current_window_slot_layout(self):
        frame=cv2.imread(str(ROOT/'tests/fixtures/patrol-0928-01/cycle-1/kill/000073.jpg'))
        result=BagVision().observe(frame)
        self.assertTrue(result['valid'])
        self.assertEqual([s['state'] for s in result['slots']], ['occupied']*14+['empty']*6)

    def test_closed_or_black_frame_is_not_a_full_bag(self):
        v=BagVision()
        for frame in [cv2.imread(str(ROOT/'tests/fixtures'/'mana-test-end.png')),np.zeros((1080,1920,3),np.uint8)]:
            result=v.observe(frame)
            self.assertFalse(result['valid'])
            self.assertNotIn('empty',result)

if __name__=='__main__':unittest.main()
