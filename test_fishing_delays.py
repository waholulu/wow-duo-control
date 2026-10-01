import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from fishing_config import load_config
from fishing_trial import random_delay
class DelayTests(unittest.TestCase):
 def test_valid_and_invalid_ranges(self):
  c=load_config(overrides={'reel_delay_ms_range':[50,150],'cycle_delay_ms_range':[0,500]})
  self.assertEqual(c['reel_delay_ms_range'],[50,150])
  for value in ([-1,100],[150,50],[0,5001],[True,100],[1.2,3]):
   with self.assertRaises(ValueError):load_config(overrides={'reel_delay_ms_range':value})
 def test_stop_prevents_wait_completion(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'STOP').touch()
   with self.assertRaisesRegex(RuntimeError,'Cancelled'):random_delay([50,150],p,time.monotonic()+1)
 def test_range_passed_to_sampler(self):
  with tempfile.TemporaryDirectory() as d,patch('fishing_trial.random.uniform',return_value=0) as sample:
   self.assertEqual(random_delay([0,500],Path(d),time.monotonic()+1),0)
   sample.assert_called_once_with(0,500)
if __name__=='__main__':unittest.main()
