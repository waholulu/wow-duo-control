import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_support import make_controller,Driver
from runtime_types import Result
from runtime_skills import recover,select_chat
class WorkflowEfficiencyTests(unittest.TestCase):
 def test_weak_sparkles_follow_nearby_grid_but_strong_corpse_template_leads(self):
  from runtime_skills import loot_search_order
  weak=[dict(x=1016,y=484,score=.64),dict(x=958,y=480,score=.64)]
  ordered=loot_search_order(weak)
  self.assertEqual(ordered[0],dict(x=1005,y=580))
  self.assertEqual(ordered[-2:],weak)
  strong=dict(x=1040,y=560,score=.72)
  self.assertEqual(loot_search_order(weak+[strong])[0],strong)

 def test_inventory_cache_expires_and_invalidates_by_identity_or_transaction(self):
  from workflow_state import InventoryCache
  cache=InventoryCache();r=Result('completed','bag',{'empty':2})
  for now,identity,expected in [(29,'a',r),(30,'a',None),(2,'b',None),(-1,'a',None)]:
   cache.remember(r,0,'a');self.assertEqual(cache.get(now,identity),expected)
  cache.remember(r,0,'a');cache.invalidate();self.assertIsNone(cache.get(1,'a'))
  cache.remember(Result('failed','unknown'),0,'a');self.assertIsNone(cache.get(1,'a'))

 def test_bounded_inventory_cache_can_cover_one_patrol_without_masking_transactions(self):
  from workflow_state import InventoryCache
  cache=InventoryCache(180);r=Result('completed','bag',{'empty':12})
  cache.remember(r,0,'paladin-profile')
  self.assertIs(cache.get(179.9,'paladin-profile'),r)
  self.assertIsNone(cache.get(180,'paladin-profile'))
  cache.remember(r,0,'paladin-profile');cache.invalidate()
  self.assertIsNone(cache.get(1,'paladin-profile'))
  for value in (0,301,True,'180'):
   with self.subTest(value=value),self.assertRaises(ValueError):InventoryCache(value)

 def test_health_only_recovery_allows_loot_but_ready_waits_for_mana(self):
  from runtime_skills import prepare
  d=Driver();d.observation.player_mana=.2;d.ctx.recovery_options={'mana_target':.7}
  r=d.run(recover(d.ctx,health_only=True));self.assertEqual(r.status,'completed');self.assertEqual(d.actions,[])
  class ManaReturns(Driver):
   def frame(self):
    self.observation.player_mana=.7 if self.now>=2 else .2
    return super().frame()
  d=ManaReturns();d.ctx.recovery_options={'mana_target':.7}
  r=d.run(prepare(d.ctx,SimpleNamespace()))
  self.assertEqual(r.status,'completed');self.assertGreaterEqual(d.now,2);self.assertEqual(d.actions,[])

 def test_ready_clears_inherited_target_before_patrol(self):
  from runtime_skills import prepare
  class InheritedTarget(Driver):
   def frame(self):
    self.observation.target=not self.actions
    self.observation.target_allowed=not self.actions
    self.observation.target_hp=.67 if not self.actions else 0
    return super().frame()
  d=InheritedTarget();d.ctx.recovery_options={'mana_target':.7}
  result=d.run(prepare(d.ctx,SimpleNamespace()))
  self.assertEqual((result.status,result.reason),('completed','ready_target_cleared'))
  self.assertEqual([a.reason for a in d.actions],['clear_target_before_patrol'])

 def test_corpse_hint_rejects_expiry_scene_shift_and_calibration_change(self):
  import numpy as np
  from dataclasses import replace
  from workflow_state import CorpseHint
  d=Driver();d.frame_image=np.zeros((1080,1920,3),np.uint8);s=d.frame();hint=CorpseHint(s,(1000,470))
  self.assertEqual(hint.points(s,1)[:3],[(1000,530),(1030,530),(970,530)])
  self.assertEqual(len(hint.points(s,1)),9)
  for changed,now in [(s,13),(s,-1),(replace(s,calibration_generation=1),1),
                       (replace(s,frame=np.full_like(d.frame_image,80)),1)]:
   self.assertEqual(hint.points(changed,now),[])

 def test_persistent_panel_never_sends_repair_clicks(self):
  for visible in (True,False):
   with self.subTest(visible=visible):
    d=Driver();d.ctx.vision_profile={'chat_tabs':{'combat':{'persistent':True}}}
    with patch('chat_tabs.selected',return_value=visible):r=d.run(select_chat(d.ctx,'combat'))
    self.assertEqual(r.status,'completed' if visible else 'failed')
    self.assertEqual(d.actions,[])
 def test_persistent_layout_rejects_old_position_and_wrong_filter(self):
  import cv2,json
  from pathlib import Path
  from chat_tabs import selected
  root=Path(__file__).resolve().parent
  evidence=root/'runs/ui-layout-trial'
  paths=[evidence/(n+'.jpg') for n in ('before','positioned','verify-0','verify-1','verify-2')]
  if not all(p.is_file() for p in paths):self.skipTest('缺少双窗口历史标定帧')
  spec=json.loads((evidence/'profile-after.json').read_text())['chat_tabs']['combat']
  for p,expected in zip(paths,(False,False,True,True,True)):
   with self.subTest(frame=p.name):self.assertEqual(bool(selected(cv2.imread(str(p)),spec)),expected)
 def test_general_panel_accepts_live_post_combat_appearance_but_rejects_shift(self):
  import cv2,json,numpy as np
  from pathlib import Path
  from chat_tabs import selected
  root=Path(__file__).resolve().parent
  paths=[root/'runs/paladin-convergence-live-09/post-run-normalized.jpg']
  paths+=list((root/'runs/paladin-convergence-live-10/evidence').glob('*/00000313.jpg'))[:1]
  if len(paths)<2 or not all(p.is_file() for p in paths):self.skipTest('缺少实战后综合窗口帧')
  spec=json.loads((root/'classes/paladin/vision/profile.json').read_text())['chat_tabs']['general']
  for path in paths:
   frame=cv2.imread(str(path));self.assertTrue(selected(frame,spec))
  shifted=np.roll(frame,20,axis=1)
  self.assertFalse(selected(shifted,spec))
 def test_compact_general_receipt_channel_is_detected_without_ui_click(self):
  import cv2,json,numpy as np
  from pathlib import Path
  from chat_tabs import selected
  profile=json.loads(Path('classes/paladin/vision/profile.json').read_text())
  spec=profile['chat_tabs']['general']
  patch_image=cv2.imread('tests/fixtures/paladin/chat-compact-current.png')
  frame=np.zeros((1080,1920,3),np.uint8)
  frame[635:840,350:470]=patch_image
  self.assertFalse(selected(frame,spec))
  self.assertTrue(selected(frame,spec['compact']))
  d=Driver();d.frame_image=frame;d.ctx.vision_profile={'chat_tabs':{'general':spec}}
  result=d.run(select_chat(d.ctx,'general'))
  self.assertEqual(result.reason,'chat_compact_receipt_channel')
  self.assertEqual(d.actions,[])
 def test_loot_invalidates_roomy_bag_but_does_not_force_roam(self):
  c=make_controller(['--kills','2']);c.workflow={'roam_after_kill':False};calls=[]
  def run(name,*a,**kw):
   calls.append(name)
   if name=='backpack':return Result('completed','backpack_confirmed',{'empty':6})
   if name=='combat':return Result('completed','xp_limit_out_of_combat',{'xp_events':1})
   return Result('completed','confirmed')
  with patch.object(c,'run_skill',side_effect=run):r=c.run()
  self.assertEqual(r.facts['confirmed_kills'],2);self.assertEqual(calls.count('backpack'),2);self.assertNotIn('roam',calls)
  self.assertEqual(calls.count('loot'),2)
 def test_low_space_still_checks_each_cycle(self):
  c=make_controller(['--kills','2']);c.workflow={'roam_after_kill':False};calls=[]
  def run(name,*a,**kw):
   calls.append(name)
   if name=='backpack':return Result('completed','backpack_confirmed',{'empty':2})
   if name=='combat':return Result('completed','xp_limit_out_of_combat',{'xp_events':1})
   return Result('completed','confirmed')
  with patch.object(c,'run_skill',side_effect=run):c.run()
  self.assertEqual(calls.count('backpack'),2)
 def test_recovery_accepts_conservative_mana_without_heal(self):
  d=Driver();d.ctx.recovery_options={'mana_target':.7};d.observation.player_mana=.7
  r=d.run(recover(d.ctx));self.assertEqual(r.status,'completed');self.assertEqual(d.actions,[])
