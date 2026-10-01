import unittest
import cv2
import numpy as np
from mineral_monitor import mineral_candidates,PersistentCandidates

class MineralTests(unittest.TestCase):
    def test_map_border_and_player_marker_are_excluded(self):
        frame=np.zeros((1080,1920,3),np.uint8)
        for pt in [(1535,280),(1582,207),(1510,250)]:
            cv2.circle(frame,pt,2,(0,220,255),-1)
        points=mineral_candidates(frame)
        self.assertEqual(len(points),1)
        self.assertEqual(points[0]['x'],1510)
    def test_flicker_is_not_persistent(self):
        t=PersistentCandidates();p=[dict(x=1510,y=250,area=5)]
        self.assertEqual(t.update(p),[]);self.assertEqual(t.update([]),[])
        self.assertEqual(t.update(p),[]);self.assertEqual(t.update(p),[])
        self.assertEqual(len(t.update(p)),1)

if __name__=='__main__':unittest.main()
