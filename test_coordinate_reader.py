import unittest
from coordinate_reader import axis_consensus

def reading(text,confidence=1):return {'items':[{'text':text,'confidence':confidence}]}

class CoordinateTests(unittest.TestCase):
    def test_single_wrong_digit_cannot_override_consensus(self):
        self.assertEqual(axis_consensus([reading(v) for v in ['27.7','27.7','21.7','27.7']]),27.7)
    def test_conflicting_or_malformed_numbers_are_rejected(self):
        for values in [['27.7','27.7','21.7','21.7'],['71-9','719','71.9','71:9'],['101.0']*4]:
            with self.assertRaises(ValueError):axis_consensus([reading(v) for v in values])
    def test_colon_in_numeric_axis_is_decimal_font_artifact(self):
        self.assertEqual(axis_consensus([reading(v) for v in ['72:0','72:0','7.2:0','72.0']]),72.)

    def test_low_confidence_cannot_form_consensus(self):
        with self.assertRaises(ValueError):axis_consensus([reading('27.7',.3)]*4)

if __name__=='__main__':unittest.main()
