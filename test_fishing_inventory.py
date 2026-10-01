import json,unittest,tempfile
from pathlib import Path
import cv2
import numpy as np
from interaction_vision import BagVision
from fishing_inventory import inventory_changed,full_notice
from fishing_config import load_config
from fishing_supervisor import recoverable_exit
ROOT=Path(__file__).resolve().parent
class InventoryTests(unittest.TestCase):
 def test_supervisor_recovers_only_verified_empty_reel(self):
  result=json.loads((ROOT/'runs/fishing-supervised-20260930-192607/session-01/result.json').read_text())
  self.assertEqual(recoverable_exit(result),'verified_no_new_loot')
  scrolled=json.loads((ROOT/'runs/fishing-supervised-20260930-200224/session-01/result.json').read_text())
  self.assertEqual(recoverable_exit(scrolled),'verified_no_new_loot')
  variant=json.loads((ROOT/'runs/fishing-supervised-20260930-204112/session-04/result.json').read_text())
  self.assertEqual(recoverable_exit(variant),'verified_no_new_loot')
  for change in ({'inventory_changed':True},{'game_inventory_full':True},{'after':{'fish':['新收获']}}):
   modified=json.loads(json.dumps(result));modified['history'][-1].update(change)
   self.assertIsNone(recoverable_exit(modified),change)
  modified=json.loads(json.dumps(result));modified['bag']['valid']=False
  self.assertIsNone(recoverable_exit(modified))
  modified=json.loads(json.dumps(scrolled));modified['history'][-1]['after']['fish'].append('你获得了战利品：［新鲜的未知鱼］')
  self.assertIsNone(recoverable_exit(modified))
 def test_game_full_message_overrides_visible_empty_slots(self):
  p=ROOT/'runs/fishing-supervised-20260928-195802/session-01/cast-13'
  c=load_config(ROOT/'calibration/fishing_until_full.json')
  with tempfile.TemporaryDirectory() as d:
   self.assertFalse(full_notice(cv2.imread(str(p/'before.jpg')),Path(d),'before',c))
   self.assertTrue(full_notice(cv2.imread(str(p/'after-04.jpg')),Path(d),'after',c))
   self.assertTrue(full_notice(cv2.imread(str(p/'after-bag.jpg')),Path(d),'confirm',c))
 def test_current_combined_bag_excludes_absent_slots(self):
  v=BagVision(json.loads((ROOT/'calibration/fishing_bag_layout.json').read_text()))
  r=v.observe(cv2.imread(str(ROOT/'runs/fishing-bag-current.jpg')))
  self.assertTrue(r['valid']);self.assertEqual((r['capacity'],r['empty'],r['occupied']),(34,12,22))
 def test_noise_is_not_stack_growth(self):
  before=np.zeros((34,33,33,3),dtype=np.uint8)+100
  self.assertFalse(inventory_changed(before,before+5))
  after=before.copy();after[0,24:28,22:27]=200
  self.assertTrue(inventory_changed(before,after))
 def test_bounded_full_mode_and_layout_required(self):
  c=load_config(ROOT/'calibration/fishing_until_full.json')
  self.assertEqual(c['stop_mode'],'bag_full')
  with self.assertRaises(ValueError):load_config(overrides={'casts':1500})
  with self.assertRaises(ValueError):load_config(ROOT/'calibration/fishing_until_full.json',{'bag_layout':'missing.json'})
if __name__=='__main__':unittest.main()
