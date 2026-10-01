import unittest
from corpse_recovery_trial import parse_ghost_coordinate

class GhostCoordinateTests(unittest.TestCase):
    def row(self,text,confidence=1):
        return {'items':[{'text':text,'confidence':confidence}]}
    def test_nameplate_line_crossing_decimal(self):
        for text in ('52.0- 37-8','52.0-37-8)','52.0-(37-8'):
            self.assertEqual(parse_ghost_coordinate(self.row(text)),(52,37.8))
    def test_reject_incomplete_or_low_confidence(self):
        for text in ('52.0--37','52.0 37.8 2','52.0-(37','52.0 378'):
            self.assertIsNone(parse_ghost_coordinate(self.row(text)))
        self.assertIsNone(parse_ghost_coordinate(self.row('52.0 37-8',.5)))
    def test_standard_format_still_accepted(self):
        self.assertEqual(parse_ghost_coordinate(self.row('49.8 (42.7)')),(49.8,42.7))
if __name__=='__main__':unittest.main()
