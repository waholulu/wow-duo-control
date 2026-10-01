import json,unittest
from pathlib import Path
from coin_receipt import coin_counts,receipt_rows,anchors,confirmed_pair,scroll_offset
class CoinScrollTests(unittest.TestCase):
 def test_live_regression(self):
  p=Path('tests/fixtures/vendor-until-full-07/cycle-5/loot')
  if not p.exists():self.fail('Recorded OCR not available')
  rows=[]
  for name in ['coin-before-ocr','coin-after-ocr-0','coin-after-ocr-1']:
   d=json.loads((p/(name+'.json')).read_text());r=coin_counts(d);r.update(_rows=receipt_rows(d),_anchors=anchors(d));rows.append(r)
  self.assertTrue(confirmed_pair(rows[0],rows[1:]))
  rows[2]['_anchors']=[]
  self.assertFalse(confirmed_pair(rows[0],rows[1:]))
 def test_one_scroll_anchor_is_insufficient(self):
  self.assertIsNone(scroll_offset({'_anchors':[dict(text='unique long anchor',y=.1)]},{'_anchors':[dict(text='unique long anchor',y=.4)]}))
 def test_no_new_receipt_no_confirmation(self):
  r={'你拾取了1铜币':1,'_rows':[dict(text='你拾取了1铜币',y=.1)]}
  self.assertFalse(confirmed_pair(r,[r,r]))
if __name__=='__main__':unittest.main()
