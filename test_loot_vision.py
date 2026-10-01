import unittest
import cv2
import numpy as np
from loot_probe import LootVision, ROOT


class LootVisionTests(unittest.TestCase):
    def test_real_cursor_states_are_distinct(self):
        v=LootVision()
        for filename,kind in [('loot-mouse-01','hand'),('loot-mouse-02','hand'),
                              ('loot-mouse-03','loot'),('loot-interact','skin')]:
            frame=cv2.imread(str(ROOT/'tests/fixtures'/f'{filename}.png'))
            self.assertEqual(v.cursor(frame)['kind'],kind)

    def test_cursor_on_changed_background(self):
        frame=cv2.imread(str(ROOT/'tests/fixtures'/'hunt-loot-01'/'loot-1'/'cursor-missing.jpg'))
        self.assertEqual(LootVision().cursor(frame)['kind'],'loot')

    def test_pointer_parked_above_world_scan_is_located(self):
        frame=cv2.imread(str(ROOT/'tests/fixtures'/'hunt-skin-03'/'cycle-1'/'loot'/'cursor-missing.jpg'))
        self.assertIsNotNone(LootVision().cursor(frame))

    def test_blank_frame_never_authorizes_cursor_or_loot(self):
        v=LootVision();frame=np.zeros((1080,1920,3),dtype=np.uint8)
        self.assertIsNone(v.cursor(frame))
        self.assertEqual(v.loot_messages(frame),0)
        self.assertEqual(v.candidates(frame),[])

    def test_proposals_remain_inside_mouse_control_area(self):
        v=LootVision()
        frame=cv2.imread(str(ROOT/'tests/fixtures'/'loot-new2.png'))
        for point in v.candidates(frame):
            self.assertTrue(450<=point['x']<=1380)
            self.assertTrue(260<=point['y']<=860)

    def test_loot_messages_only_appear_after_real_pickup(self):
        v=LootVision()
        before=cv2.imread(str(ROOT/'tests/fixtures'/'loot-mouse-03.png'))
        after=cv2.imread(str(ROOT/'tests/fixtures'/'loot-interact.png'))
        self.assertEqual(v.loot_messages(before),0)
        self.assertEqual(v.loot_messages(after),2)

if __name__=='__main__':unittest.main()
