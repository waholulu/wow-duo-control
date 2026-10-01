import time
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from window_calibration import WindowCalibration,ROOT
from vision_state import Vision
from vision_feed import Feed


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference=cv2.imread(str(ROOT/'tests/fixtures/patrol-hunt-04/cycle-5/hunt/cycle-1/loot/success.jpg'))
        if cls.reference is None:raise AssertionError('Required recorded source frame unavailable')

    def test_move_scale_and_capture_resolutions(self):
        for scale,tx,ty,size in [(1,-200,-100,(1920,1080)),(.7,-150,-50,(1280,720)),
                                  (1.4,-300,-100,(2560,1440)),(.5,300,100,(1366,768))]:
            with self.subTest(scale=scale,size=size):
                raw=cv2.warpAffine(self.reference,np.float32([[scale,0,tx],[0,scale,ty]]),size)
                calibration=WindowCalibration();self.assertTrue(calibration.acquire(raw))
                normalized=calibration.normalize(raw)
                self.assertIsNotNone(normalized)
                self.assertTrue(Vision(ROOT/'calibration/profile.json').observe(normalized).valid)
                s,x,y=calibration.transform
                self.assertAlmostEqual(s,scale,delta=.006)
                self.assertAlmostEqual(x,tx,delta=2)
                self.assertAlmostEqual(y,ty,delta=2)

    def test_actual_wake_letterbox(self):
        raw=cv2.imread(str(ROOT/'tests/fixtures/wake-check-latest.jpg'))
        if raw is None:self.fail('Recorded wake frame unavailable')
        c=WindowCalibration();self.assertTrue(c.acquire(raw))
        o=Vision(ROOT/'calibration/profile.json').observe(c.normalize(raw))
        self.assertTrue(o.valid);self.assertGreater(o.player_hp,.95)

    def test_verified_night_layout_with_separate_axis_scales(self):
        raw=cv2.imread(str(ROOT/'tests/fixtures/resume-0928.jpg'))
        if raw is None:self.fail('Recorded night layout unavailable')
        c=WindowCalibration();self.assertTrue(c.acquire(raw))
        self.assertEqual(len(c.transform),4)
        o=Vision(ROOT/'calibration/profile.json').observe(c.normalize(raw))
        self.assertTrue(o.valid);self.assertGreater(o.player_hp,.95)
        sx,sy,_,_=c.transform
        self.assertEqual(c.mouse_delta(50,50),(round(50*sx),round(50*sy)))

    def test_occlusion_invalidates_old_transform(self):
        c=WindowCalibration();self.assertIsNotNone(c.normalize(self.reference))
        raw=self.reference.copy();raw[185:225,1560:1610]=0
        self.assertIsNone(c.normalize(raw));self.assertIsNone(c.transform)
        with self.assertRaises(RuntimeError):c.mouse_delta(20,10)

    def test_recorded_combat_animation_does_not_break_registration(self):
        files=sorted((ROOT/'tests/fixtures/patrol-hunt-04').glob('cycle-*/hunt/cycle-1/kill/*.jpg'))
        if not files:self.fail('Combat replay unavailable')
        c=WindowCalibration()
        for path in files[::max(1,len(files)//35)]:
            with self.subTest(path=path.name):
                self.assertIsNotNone(c.normalize(cv2.imread(str(path))))

    def test_current_paladin_combat_ui_reacquires(self):
        raw=cv2.imread(str(ROOT/'runs/paladin-convergence-live-01-post-raw.jpg'))
        if raw is None:self.skipTest('缺少当前圣骑士战斗窗口原帧')
        c=WindowCalibration();self.assertTrue(c.acquire(raw))
        normalized=c.normalize(raw);self.assertIsNotNone(normalized)
        observation=Vision(ROOT/'classes/paladin/vision/profile.json').observe(normalized)
        self.assertTrue(observation.valid);self.assertGreaterEqual(observation.player_hp,.95)

    def test_paladin_combat_indicator_rejects_red_world_occlusion(self):
        false_paths=list((ROOT/'runs/paladin-convergence-live-11/evidence').glob('*/00000048.jpg'))
        true_paths=list((ROOT/'runs/paladin-convergence-live-09/evidence').glob('*/00000379.jpg'))
        if not false_paths or not true_paths:self.skipTest('缺少圣骑士战斗 ROI 回放证据')
        vision=Vision(ROOT/'classes/paladin/vision/profile.json')
        self.assertFalse(vision.combat(cv2.imread(str(false_paths[0]))))
        self.assertTrue(vision.combat(cv2.imread(str(true_paths[0]))))

    def test_black_and_unrelated_desktop_rejected(self):
        c=WindowCalibration()
        for raw in (np.zeros_like(self.reference),np.full_like(self.reference,140)):
            self.assertFalse(c.acquire(raw));self.assertIsNone(c.normalize(raw))

    def test_moved_window_reacquires_without_old_coordinates(self):
        c=WindowCalibration();self.assertIsNotNone(c.normalize(self.reference))
        moved=cv2.warpAffine(self.reference,np.float32([[1,0,-150],[0,1,-100]]),(1920,1080))
        self.assertIsNone(c.normalize(moved));self.assertTrue(c.acquire(moved))
        self.assertIsNotNone(c.normalize(moved))

    def test_mouse_scaling_and_freshness(self):
        c=WindowCalibration();c.transform=(.7,120,80)
        self.assertEqual(c.mouse_delta(30,-20),(21,-14))
        feed=Feed.__new__(Feed);feed.calibration=c;feed.calibration_checked_at=0
        with self.assertRaises(RuntimeError):feed.mouse_delta(30,20)
        feed.calibration_checked_at=time.monotonic()
        self.assertEqual(feed.mouse_delta(30,-20),(21,-14))

    def test_acquisition_frame_is_never_returned_for_input(self):
        feed=Feed.__new__(Feed);feed.calibration=WindowCalibration();feed.calibration_checked_at=0
        moved=cv2.warpAffine(self.reference,np.float32([[1,0,-150],[0,1,-100]]),(1920,1080))
        with patch.object(feed,'raw_frame',side_effect=[(moved,.05),(moved,.05)]) as raw:
            result,age=feed.frame()
        self.assertEqual(raw.call_count,2);self.assertLess(age,.5)
        self.assertTrue(Vision(ROOT/'calibration/profile.json').observe(result).valid)


if __name__=='__main__':unittest.main()
