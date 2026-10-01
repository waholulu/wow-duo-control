import unittest
from navigation_coordinate import parse
class CoordinateNavigationTests(unittest.TestCase):
 def test_calibrated_punctuation_variants(self):
  for text in ['24.2 (73.60','24.2(73.6)','24,2 ((73.6)','24:2 (73,6).']:
   self.assertEqual(parse({'items':[{'text':text,'confidence':1}]}),(24.2,73.6))
 def test_ambiguous_or_low_confidence_rejected(self):
  for text in ['24.2173.61','player 24.2 73.6','24.2 73.6 5']:
   self.assertIsNone(parse({'items':[{'text':text,'confidence':1}]}))
  self.assertIsNone(parse({'items':[{'text':'24.2 73.6','confidence':.5}]}))
if __name__=='__main__':unittest.main()
