"""Regression: receipt prefixes missed in full-chat OCR stay required in row OCR."""
import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
from skin_receipt import read_skin_receipts, increased

class SkinRowReplay(unittest.TestCase):
    def test_recorded_missed_prefix_is_recovered_on_both_after_frames(self):
        source=Path('tests/fixtures/hunt-skin-11/cycle-1/skin')
        if not (source/'skin-before-ocr.png').exists():
            self.fail('Recorded regression frames not available')
        with tempfile.TemporaryDirectory() as folder:
            counts=[]
            for label in ['skin-before-ocr','skin-after-ocr-0','skin-after-ocr-1']:
                crop=cv2.imread(str(source/(label+'.png')))
                frame=np.zeros((1080,1920,3),np.uint8)
                frame[670:830,398:778]=cv2.resize(crop,(380,160))
                counts.append(read_skin_receipts(frame,folder,label))
            self.assertEqual(counts[0]['破烂的皮革'],0)
            self.assertTrue(all(increased(counts[0],after) for after in counts[1:]))

if __name__=='__main__':unittest.main()
