from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from runtime_types import Context, Observe, Wait, Work, Intent, Snapshot, Result, GuardFailed
from vision_state import Observation
from runtime_skills import backpack, loot, navigate, move_pointer
from runtime_audio import fishing
from runtime_interactions import sell_junk
from runtime_main import Controller, parser


from test_support import Driver, immediate, make_controller


class SkillReplayTests(unittest.TestCase):
    def test_pointer_missing_can_use_calibrated_absolute_reacquisition(self):
        driver=Driver();driver.ctx.vision_profile={'absolute_cursor_reacquire':{'screen_size':[1920,1080]}}
        def cursor(_):
            if not driver.actions:return None
            return dict(x=1000,y=600,kind='hand')
        result=driver.run(move_pointer(driver.ctx,SimpleNamespace(cursor=cursor),1000,600,
                                       expected='hand'))
        self.assertEqual(result[1]['kind'],'hand')
        self.assertEqual([(a.kind,a.values,a.reason) for a in driver.actions],
                         [('move_to',(1000,600,1920,1080),'reacquire_pointer_absolute')])

    def test_pointer_missing_uses_bounded_closed_loop_reacquisition(self):
        driver=Driver()
        def cursor(_):
            if len(driver.actions)<2:return None
            return dict(x=1000,y=600,kind='hand')
        result=driver.run(move_pointer(driver.ctx,SimpleNamespace(cursor=cursor),1000,600,
                                       expected='hand'))
        self.assertEqual(result[1]['kind'],'hand')
        self.assertEqual([(a.kind,a.values,a.reason) for a in driver.actions],
                         [('move',(12,0),'reacquire_pointer'),
                          ('move',(-12,0),'reacquire_pointer')])

    def test_pointer_reacquisition_never_clicks_and_stops_after_closed_loop(self):
        driver=Driver()
        with self.assertRaisesRegex(GuardFailed,'cursor_not_located'):
            driver.run(move_pointer(driver.ctx,SimpleNamespace(cursor=lambda _:None),1000,600))
        self.assertEqual([a.values for a in driver.actions],
                         [(12,0),(-12,0),(0,12),(0,-12)])
        self.assertTrue(all(a.kind=='move' and a.reason=='reacquire_pointer'
                            for a in driver.actions))

    def test_pointer_reacquisition_refreshes_authority_after_slow_recognition(self):
        driver=Driver()
        def cursor(_):
            driver.now+=.6
            return None
        with self.assertRaisesRegex(GuardFailed,'cursor_not_located'):
            driver.run(move_pointer(driver.ctx,SimpleNamespace(cursor=cursor),1000,600))
        self.assertEqual(len(driver.actions),4)
        self.assertTrue(all(a.snapshot.captured_at>=.6 for a in driver.actions))

    def test_pointer_confirmation_reacquires_if_recognition_makes_frame_stale(self):
        driver=Driver();costs=iter((0,.6,0,0))
        def cursor(_):
            driver.now+=next(costs,0)
            return dict(x=1000,y=600,kind='loot')
        result=driver.run(move_pointer(driver.ctx,SimpleNamespace(cursor=cursor),1000,600,
                                       expected='loot'))
        snapshot,confirmed=result
        self.assertLessEqual(driver.now-snapshot.captured_at,.45)
        self.assertEqual(confirmed['kind'],'loot')
        self.assertLess(driver.now,.95)

    def test_backpack_requires_three_equal_frames_and_observed_closure(self):
        driver=Driver()
        valid=dict(open=True,valid=True,empty=7,slots=[{'state':'empty'}])
        readings=iter([{'open':False},valid,valid,valid,{'open':True},{'open':False}])
        bag=SimpleNamespace(observe=lambda _:next(readings))
        cursor=SimpleNamespace(cursor=lambda _:dict(x=800,y=250,kind='hand'))
        with patch('runtime_skills.BagVision',return_value=bag),patch('runtime_skills.LootVision',return_value=cursor):
            result=driver.run(backpack(driver.ctx))
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.facts['empty'],7)
        self.assertTrue(result.facts['closed_verified'])
        self.assertEqual([a.reason for a in driver.actions],['open_backpack','close_backpack'])

    def test_backpack_can_validate_after_unconfirmed_absolute_pointer_park(self):
        driver=Driver();driver.ctx.vision_profile={
            'absolute_cursor_reacquire':{'screen_size':[1920,1080]},
            'backpack_pointer_park':[800,250]}
        valid=dict(open=True,valid=True,empty=7,slots=[{'state':'empty'}])
        readings=iter([{'open':False},valid,valid,valid,{'open':True},{'open':False}])
        bag=SimpleNamespace(observe=lambda _:next(readings))
        cursor=SimpleNamespace(cursor=lambda _:None)
        with patch('runtime_skills.BagVision',return_value=bag),patch('runtime_skills.LootVision',return_value=cursor):
            result=driver.run(backpack(driver.ctx))
        self.assertEqual(result.status,'completed')
        self.assertEqual([(a.kind,a.reason) for a in driver.actions],
                         [('move_to','reacquire_pointer_absolute'),
                          ('tap','open_backpack'),('tap','close_backpack')])

    def test_backpack_never_opens_in_combat(self):
        driver=Driver();driver.observation.in_combat=True
        with self.assertRaises(GuardFailed):driver.run(backpack(driver.ctx))
        self.assertEqual(driver.actions,[])

    def test_loot_click_needs_cursor_and_success_needs_new_evidence(self):
        for same in (True,False):
            driver=Driver()
            def cursor(_):
                if driver.actions:return dict(x=1000 if same else 1050,y=600,kind='skin')
                return dict(x=1000,y=600,kind='loot')
            visual=SimpleNamespace(cursor=cursor,loot_messages=lambda _:2)
            with tempfile.TemporaryDirectory() as t,patch('runtime_skills.LootVision',return_value=visual),patch('runtime_skills.read_loot_receipts',return_value={}):
                result=driver.run(loot(driver.ctx,t,point=(1000,600)))
            self.assertEqual(len(driver.actions),1)
            self.assertEqual(driver.actions[0].reason,'loot')
            self.assertEqual(result.status,'completed' if same else 'failed')
            if same:self.assertFalse(result.facts['corpse_empty_verified'])

    def test_navigation_no_displacement_stops_without_arrival(self):
        driver=Driver()
        with patch('runtime_skills.location',side_effect=lambda *a:immediate((1,1))):
            result=driver.run(navigate(driver.ctx,'unused',[(2,2)]))
        self.assertEqual(result.reason,'route_obstructed')
        self.assertEqual([a.reason for a in driver.actions],['navigation_move']*4)

    def test_late_receipt_requires_two_new_frames_after_camera_moves(self):
        driver=Driver()
        def cursor(_):
            return dict(x=1050 if driver.actions else 1000,y=600,
                        kind='hand' if driver.actions else 'loot')
        visual=SimpleNamespace(cursor=cursor,loot_messages=lambda _:0)
        receipts=[{}, {}, {'你拾取了1铜币':1}, {'你拾取了1铜币':1}]
        with tempfile.TemporaryDirectory() as t,patch('runtime_skills.LootVision',return_value=visual),\
             patch('runtime_skills.read_loot_receipts',side_effect=receipts) as reader:
            result=driver.run(loot(driver.ctx,t,point=(1000,600)))
        self.assertEqual(result.reason,'new_personal_loot_receipt_two_frames')
        self.assertEqual(reader.call_count,4)
        self.assertEqual(len(driver.actions),1)

    def test_navigation_resume_uses_checkpoint_and_new_location(self):
        driver=Driver();driver.ctx.checkpoint['waypoint']=1
        with patch('runtime_skills.location',side_effect=lambda *a:immediate((3,4))):
            result=driver.run(navigate(driver.ctx,'unused',[(1,2),(3,4)]))
        self.assertEqual(result.reason,'ARRIVED')
        self.assertEqual(driver.actions,[])

    def test_fishing_duplicate_signal_reels_only_once(self):
        driver=Driver()
        position=dict(x=1000,y=600,kind='hand')
        visual=SimpleNamespace(cursor=lambda *a:position)
        audio=SimpleNamespace(healthy=lambda:True,error=None,since=lambda *a:[SimpleNamespace(at=driver.now,identity='same-bite')])
        spec=dict(cast_key=31,receipt_items=['鱼'],bobber_template='unused',world_roi=[1,2,3,4],wait_seconds=5)
        with patch('runtime_audio.LootVision',return_value=visual),patch('runtime_audio.template',return_value=position),\
             patch('runtime_audio.read_receipts',side_effect=lambda *a:immediate({})),\
             patch('runtime_audio.receipt_confirmation',side_effect=lambda *a:immediate(True)):
            result=driver.run(fishing(driver.ctx,spec,audio,'unused',1))
        self.assertEqual(result.facts['received'],1)
        self.assertEqual([a.reason for a in driver.actions],['cast_fishing','reel_fishing'])

    def test_audio_missing_never_casts_without_validated_visual_mode(self):
        driver=Driver()
        result=driver.run(fishing(driver.ctx,{},None,'unused',1))
        self.assertEqual(result.reason,'audio_unavailable')
        self.assertEqual(driver.actions,[])

    def test_sale_rescans_and_requires_money_plus_empty_slot(self):
        for confirm_empty in (True,False):
            driver=Driver()
            driver.frame_image=np.zeros((1080,1920,3),np.uint8)
            cursor_position=[1000,600]
            def sold():return sum(a.reason=='sell_approved_gray_junk' for a in driver.actions)
            spec=dict(name='杂货商',window_template='window',window_roi=[0,0,1,1],
                      money_roi=[1,0,1,1],item_tooltip_roi=[2,0,1,1],
                      merchant_points=[(1000,600)],slots=[dict(roi=[3,0,1,1],point=[1500,700],empty_template='empty')],
                      allowed_junk=[dict(name='断牙',category='junk',quality='poor',approved=True,icon='tooth')])
            def matching(frame,path,*args):
                if path=='window':return {'score':1}
                if path=='tooth':return {'score':1} if sold()==0 else None
                if path=='empty':return {'score':1} if sold() and confirm_empty else None
                raise AssertionError(path)
            def read(frame,roi,folder,label):
                text='杂货商' if label.startswith('vendor') else '断牙'
                if label.startswith('money'):text=f'{10+sold()}铜币'
                return {'items':[dict(text=text,confidence=1)]}
            def pointer(ctx,vision,x,y,**kw):
                cursor_position[:]=[x,y]
                return immediate((driver.frame(),{}))
            cursor=SimpleNamespace(cursor=lambda _:dict(x=cursor_position[0],y=cursor_position[1],kind='hand'))
            with patch('runtime_interactions.template',side_effect=matching),patch('runtime_interactions.ocr',side_effect=read),\
                 patch('runtime_interactions.LootVision',return_value=cursor),\
                 patch('runtime_interactions.move_pointer',side_effect=pointer):
                result=driver.run(sell_junk(driver.ctx,spec,'unused'))
            self.assertEqual(sold(),1)
            self.assertEqual(result.status,'completed' if confirm_empty else 'failed')
            if not confirm_empty:self.assertEqual(result.reason,'sale_effect_unconfirmed')


class CompositionTests(unittest.TestCase):
    make = staticmethod(make_controller)

    def test_search_rounds_do_not_consume_kill_count_or_roam_after_final_kill(self):
        controller=self.make(['--no-loot','--kills','2'])
        fights=iter([Result('failed','no_targets_found'),Result('completed','xp_limit_out_of_combat',{'xp_events':1}),Result('completed','xp_limit_out_of_combat',{'xp_events':1})])
        calls=[]
        def run(name,*args,**kw):
            calls.append(name)
            return next(fights) if name=='combat' else Result('completed','STEP_COMPLETE')
        with patch.object(controller,'run_skill',side_effect=run):result=controller.run()
        self.assertEqual(result.reason,'KILL_LIMIT_COMPLETE')
        self.assertEqual(result.facts['confirmed_kills'],2)
        self.assertEqual(calls.count('combat'),3)
        self.assertEqual(calls.count('roam'),2)

    def test_all_classes_keep_search_roam_kill_and_confirmed_loot(self):
        for character in ('warlock','paladin'):
            with self.subTest(character=character):
                controller=self.make(['--class',character,'--kills','1'])
                fights=iter([Result('failed','no_targets_found'),
                             Result('completed','xp_limit_out_of_combat',{'xp_events':1})])
                calls=[]
                def run(name,*args,**kw):
                    calls.append(name)
                    if name=='ready':return Result('completed','ready')
                    if name=='backpack':return Result('completed','backpack_confirmed',{'empty':5})
                    if name=='combat':return next(fights)
                    if name=='loot':return Result('completed','new_loot_messages_two_frames')
                    return Result('completed','STEP_COMPLETE')
                with patch.object(controller,'run_skill',side_effect=run):
                    result=controller.run()
                self.assertEqual(result.reason,'KILL_LIMIT_COMPLETE')
                self.assertEqual(calls,['ready','backpack','combat','roam','ready','combat','recover_before_loot','loot'])

    def test_all_classes_stop_before_roaming_when_loot_is_unconfirmed(self):
        for character in ('warlock','paladin'):
            for picked in (Result('skipped','no_verified_corpse'),
                           Result('failed','no_new_loot_messages'),
                           Result('cancelled','stop_requested')):
                with self.subTest(character=character,picked=picked.reason):
                    controller=self.make(['--class',character,'--kills','1'])
                    calls=[]
                    def run(name,*args,**kw):
                        calls.append(name)
                        if name=='ready':return Result('completed','ready')
                        if name=='backpack':return Result('completed','backpack_confirmed',{'empty':5})
                        if name=='combat':return Result('completed','xp_limit_out_of_combat',{'xp_events':1})
                        if name=='recover_before_loot':return Result('completed','recovered')
                        if name=='loot':return picked
                        self.fail('Must not roam after unconfirmed loot')
                    with patch.object(controller,'run_skill',side_effect=run):
                        result=controller.run()
                    self.assertEqual(result.reason,'stop_requested' if picked.status=='cancelled' else 'loot_unconfirmed')
                    self.assertEqual(calls,['ready','backpack','combat','recover_before_loot','loot'])

    def test_failed_recovery_preserves_kill_and_prevents_loot_or_roam(self):
        controller=self.make(['--kills','1'])
        calls=[]
        def run(name,*args,**kw):
            calls.append(name)
            if name=='ready':return Result('completed','ready')
            if name=='backpack':return Result('completed','backpack_confirmed',{'empty':5})
            if name=='combat':return Result('completed','xp_limit_out_of_combat',{'xp_events':1})
            if name=='recover_before_loot':return Result('failed','combat_restarted_during_recovery')
            self.fail('Recovery failure must prevent further input skills')
        with patch.object(controller,'run_skill',side_effect=run):result=controller.run()
        self.assertEqual(result.reason,'recovery_before_loot_failed')
        self.assertEqual(result.facts['confirmed_kills'],1)
        self.assertEqual(calls,['ready','backpack','combat','recover_before_loot'])

    def test_uncertain_backpack_prevents_all_following_skills(self):
        controller=self.make([])
        with patch.object(controller,'run_skill',side_effect=[Result('completed','ready'),Result('failed','backpack_unconfirmed')]) as run:
            result=controller.run()
        self.assertEqual(result.reason,'backpack_unconfirmed')
        self.assertEqual([c.args[0] for c in run.call_args_list],['ready','backpack'])

    def test_until_full_does_not_visit_vendor(self):
        controller=self.make(['--task','hunt','--until-full'])
        with patch.object(controller,'run_skill',return_value=Result('completed','backpack_confirmed',{'empty':0})),\
             patch.object(controller,'merchant_trip') as vendor:
            result=controller.run()
        self.assertEqual(result.reason,'BAG_FULL')
        vendor.assert_not_called()

    def test_vendor_failure_disables_loot_and_continues_kill_only(self):
        controller=self.make(['--kills','1'])
        calls=[]
        def run(name,*args,**kw):
            calls.append(name)
            if name=='backpack':return Result('completed','backpack_confirmed',{'empty':0})
            return Result('completed','xp_limit_out_of_combat',{'xp_events':1})
        def vendor():
            controller.looting=False
            return Result('skipped','vendor_failed_kill_only')
        with patch.object(controller,'run_skill',side_effect=run),patch.object(controller,'merchant_trip',side_effect=vendor):
            result=controller.run()
        self.assertEqual(result.reason,'KILL_LIMIT_COMPLETE')
        self.assertEqual(calls,['ready','backpack','ready','combat'])


if __name__=='__main__':unittest.main()

class CombatCompletionPriorityTest(unittest.TestCase):
    def test_confirmed_kill_at_deadline_still_reaches_loot(self):
        from runtime_skills import combat
        from control_policy import Policy
        d=Driver(); p=Policy(); p.xp_events=1
        scheduler=SimpleNamespace(combat_options={},note_engagement=lambda _:None)
        with patch('runtime_skills.Policy',return_value=p):
            result=d.run(combat(d.ctx,scheduler,seconds=0))
        self.assertEqual((result.status,result.reason),('completed','xp_limit_out_of_combat'))

class FailedPullRecoveryTest(unittest.TestCase):
    def test_clear_requires_three_peaceful_frames(self):
        from runtime_skills import disengage_unreachable
        d=Driver()
        result=d.run(disengage_unreachable(d.ctx))
        self.assertEqual(result.reason,'failed_pull_cleared')
        self.assertGreaterEqual(d.sequence,4)

    def test_persistent_combat_does_not_resume_patrol(self):
        from runtime_skills import disengage_unreachable
        d=Driver();d.observation.in_combat=True;d.observation.target=True
        result=d.run(disengage_unreachable(d.ctx))
        self.assertEqual(result.reason,'still_in_combat_after_clear')
        self.assertEqual([a.reason for a in d.actions],['clear_unreachable_target'])
