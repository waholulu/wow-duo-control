import unittest,math
from navigate_local import steering,oscillating
class SteeringTests(unittest.TestCase):
 def test_repeated_route_cycle_stops_but_progress_does_not(self):
  self.assertTrue(oscillating([(25,73.1),(25,73.2)]*2))
  self.assertFalse(oscillating([(25,73.1),(25,73.2),(24.9,73.2),(24.8,73.2)]))
 def test_downward_map_direction_turns_right(self):
  self.assertAlmostEqual(steering((0,0),0,(0,1)),math.pi/2)
 def test_wraps_short_way(self):
  self.assertAlmostEqual(steering((0,0),math.radians(179),(math.cos(math.radians(-179)),math.sin(math.radians(-179)))),math.radians(2))
 def test_aligned_needs_no_turn(self):
  self.assertAlmostEqual(steering((24,73),0,(30,73)),0)
if __name__=='__main__':unittest.main()
