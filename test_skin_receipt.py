import unittest
from skin_receipt import skin_counts,increased

class ReceiptTests(unittest.TestCase):
    def test_scrolled_chat_and_corrupted_brackets(self):
        before=skin_counts({'items':[{'text':'你获得了战利品：[破烂的皮革]','confidence':.5}]})
        after=skin_counts({'items':[{'text':'你获得了限利品：[破烂的皮革]','confidence':.5},
                                   {'text':'你获得了股利品：[破烂的皮革1','confidence':.3}]})
        self.assertTrue(increased(before,after))
        self.assertFalse(increased(before,before))
    def test_scrolling_keeps_count_but_appends_a_new_skin_receipt(self):
        before={'破烂的皮革':1,'_receipt_order':['破烂的皮革','破碎的爪子']}
        after={'破烂的皮革':1,'_receipt_order':['破碎的爪子','破烂的皮革']}
        self.assertTrue(increased(before,after))
        self.assertFalse(increased(before,before))
    def test_no_receipt_overlap_is_not_proof(self):
        self.assertFalse(increased({'破烂的皮革':1,'_receipt_order':['断牙']},
                                   {'破烂的皮革':1,'_receipt_order':['破烂的皮革']}))

    def test_player_chat_or_low_confidence_is_not_a_receipt(self):
        r=skin_counts({'items':[{'text':'[1] 玩家：你获得了破烂的皮革','confidence':1},
                               {'text':'你获得了破烂的皮革','confidence':.1}]})
        self.assertEqual(r['破烂的皮革'],0)

if __name__=='__main__':unittest.main()
