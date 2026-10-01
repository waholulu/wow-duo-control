import unittest
from mineral_hover import copper_name

class TooltipTests(unittest.TestCase):
    def test_exact_copper_name_and_confidence_required(self):
        self.assertEqual(copper_name({'items':[{'text':'铜矿','confidence':.9}]}),'铜矿')
        for text,confidence in [('铜矿',.3),('寻找矿物',1),('铜矿石',1),('蓬毛幼狼',1),('需要铜矿',1)]:
            self.assertIsNone(copper_name({'items':[{'text':text,'confidence':confidence}]}))
    def test_missing_ocr_never_confirms(self):
        self.assertIsNone(copper_name({'error':'ocr_failed'}))

if __name__=='__main__':unittest.main()
