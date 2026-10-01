from test_support import requires_archive
import unittest
from coin_receipt import coin_counts,increased
class CoinReceiptTests(unittest.TestCase):
 def test_personal_receipt_only(self):
  data={'items':[{'text':'你拾取了3铜币','confidence':1},{'text':'玩家：你拾取了3铜币','confidence':1},{'text':'你拾取了5铜币','confidence':.3}]}
  self.assertEqual(coin_counts(data),{'你拾取了3铜币':1})
 def test_unchanged_does_not_confirm(self):
  before={'你拾取了3铜币':1}
  self.assertFalse(increased(before,before));self.assertTrue(increased(before,{'你拾取了3铜币':2}))
if __name__=='__main__':unittest.main()

class ItemReceiptTest(unittest.TestCase):
    def test_two_new_personal_item_receipts_confirm_loot(self):
        from coin_receipt import loot_counts,confirmed_pair
        before=loot_counts({'items':[{'text':'[1.综合]你获得了战利品：假消息','confidence':1}]})
        after=loot_counts({'items':[{'text':'你获得了战利品：「新鲜的红苹果1','confidence':1}]})
        self.assertEqual(before,{})
        self.assertTrue(confirmed_pair(before,[after,after]))
        self.assertFalse(confirmed_pair(after,[after,after]))
    @requires_archive('runs/paladin-cv-twenty-29/0022-loot/before.json', 'runs/paladin-cv-twenty-29/0022-loot/after-0.json', 'runs/paladin-cv-twenty-29/0022-loot/after-1.json')
    def test_recorded_claw_receipt_confirms_despite_confidence_variation(self):
        import json
        from pathlib import Path
        from coin_receipt import loot_counts,confirmed_pair
        root=Path('runs/paladin-cv-twenty-29/0022-loot')
        before=loot_counts(json.loads((root/'before.json').read_text()))
        after=[loot_counts(json.loads((root/f'after-{i}.json').read_text())) for i in (0,1)]
        self.assertTrue(confirmed_pair(before,after))
        self.assertFalse(confirmed_pair(before,[before,before]))
        self.assertFalse(loot_counts({'items':[{'text':'你获得了战利品：[物品]','confidence':.3}]}))
