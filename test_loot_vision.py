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

class TemporalSparkleTests(unittest.TestCase):
    def frames(self, blink=True):
        frames=[]
        for n in range(4):
            f=np.zeros((1080,1920,3),np.uint8)
            # Bright static terrain and a stationary small yellow star.
            cv2.rectangle(f,(850,480),(890,530),(0,220,255),-1)
            cv2.circle(f,(930,540),2,(0,240,255),-1)
            for x,y in [(1000,580),(1014,584),(1007,595)]:
                cv2.circle(f,(x+(n%2)*3 if blink else x,y),2,(0,240,255),-1)
            frames.append(f)
        return frames

    def test_blinking_cluster_is_localized_but_static_lights_are_rejected(self):
        points=LootVision().sparkle_candidates(self.frames())
        self.assertEqual(len(points),1)
        self.assertLess(abs(points[0]['x']-1008),15)
        self.assertLess(abs(points[0]['y']-602),15)
        self.assertEqual(points[0]['source'],'temporal_sparkles')
        self.assertEqual(LootVision().sparkle_candidates(self.frames(False)),[])

    def test_scene_change_invalidates_temporal_positions(self):
        frames=self.frames()
        frames[-1][450:875,790:1390]=180
        self.assertEqual(LootVision().sparkle_candidates(frames),[])

    def test_one_frame_flash_is_not_a_supported_corpse_cluster(self):
        frames=[np.zeros((1080,1920,3),np.uint8) for _ in range(4)]
        cv2.circle(frames[0],(1000,580),3,(0,240,255),-1)
        self.assertEqual(LootVision().sparkle_candidates(frames),[])

class CurrentSceneSafetyReplayTests(unittest.TestCase):
    def vision(self):
        from vision_state import Vision
        return Vision(ROOT/'classes/paladin/vision/profile.json')

    def name_frame(self,name):
        frame=np.zeros((1080,1920,3),np.uint8)
        patch=cv2.imread(str(ROOT/'tests/fixtures/paladin/name-xp-20261007'/name))
        frame[660:677,1289:1385]=patch
        return frame

    def test_excluded_laborer_rejected_even_with_prior_approved_label(self):
        v=self.vision()
        self.assertTrue(v.allowed_target(self.name_frame('approved-vermin.png'),True))
        for name in ('laborer-99.png','laborer-108.png'):
            v.target_label='allowed_name_kobold_vermin'
            self.assertFalse(v.allowed_target(self.name_frame(name),True))
            self.assertIsNone(v.target_label)

    def test_blue_to_purple_experience_preserves_real_growth(self):
        v=self.vision();values=[]
        for name in ('xp-153.png','xp-159.png'):
            frame=np.zeros((1080,1920,3),np.uint8)
            frame[889:898,486:1575]=cv2.imread(str(ROOT/'tests/fixtures/paladin/name-xp-20261007'/name))
            values.append(v.bar(frame,'experience'))
        self.assertGreater(values[1]-values[0],.004)
        self.assertLess(values[1]-values[0],.15)
        self.assertEqual(v.bar(np.zeros((1080,1920,3),np.uint8),'experience'),0)

if __name__=='__main__':unittest.main()
