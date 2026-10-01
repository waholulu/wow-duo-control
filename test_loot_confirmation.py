import unittest
from pathlib import Path


class CursorSnowRegression(unittest.TestCase):
    def test_smaller_cursor_after_window_normalization(self):
        import cv2
        from loot_probe import LootVision
        path=Path('tests/fixtures/bag-0928-02/pointer-unrecognized.jpg')
        if not path.exists():self.fail('Recorded cursor frame unavailable')
        vision=LootVision();frame=cv2.imread(str(path))
        for _ in range(2):
            cursor=vision.cursor(frame)
            self.assertEqual(cursor['kind'],'hand');self.assertEqual(cursor['cursor_scale'],.8)
            self.assertLess(abs(cursor['x']-1088),3);self.assertLess(abs(cursor['y']-833),3)
    def test_recorded_snow_background_cursor(self):
        import cv2
        from loot_probe import LootVision
        path=Path('tests/fixtures/patrol-hunt-02/cycle-4/hunt/cycle-1/loot/cursor-missing.jpg')
        if not path.exists():self.fail('Recorded regression frame unavailable')
        cursor=LootVision().cursor(cv2.imread(str(path)))
        self.assertEqual(cursor['kind'],'loot')
        self.assertLess(cursor['score'],.92)
        self.assertGreaterEqual(cursor['core_score'],.965)
        self.assertLess(abs(cursor['x']-987),3)

if __name__=='__main__':unittest.main()
