import unittest,threading
from collections import deque
import numpy as np
from fishing_audio import FishingAudio,host_seconds

class FishingAudioTests(unittest.TestCase):
 def source(self):
  s=FishingAudio.__new__(FishingAudio);s.lock=threading.Lock();s.error=None;s.recent=deque();return s
 def test_window_does_not_use_future_samples(self):
  s=self.source();s.recent.append((1.,1.1,np.ones(1600)*.01));s.recent.append((1.1,1.2,np.ones(1600)*.9))
  result=s.window(1.1,.1);self.assertAlmostEqual(result['rms50_max'],.01);self.assertTrue(result['record_only'])
 def test_short_burst_not_diluted_into_whole_half_second(self):
  s=self.source();s.recent.append((0.,.5,np.r_[np.zeros(7200),np.ones(800)*.02]))
  result=s.window(.5);self.assertAlmostEqual(result['rms50_max'],.02);self.assertLess(result['rms'],.007)
 def test_error_disables_audio_evidence(self):
  s=self.source();s.recent.append((0.,.1,np.ones(1600)));s.error='gap';self.assertFalse(s.window(.1)['available'])
if __name__=='__main__':unittest.main()
