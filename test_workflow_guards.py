"""Offline failure replays for workflow boundaries; no hardware constructors."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from runtime_types import GuardFailed, Result
from runtime_main import Controller, parser, check_profile, legacy_entry
from runtime_skills import combat, location, navigate
from runtime_interactions import close_merchant, sale_frame_unchanged, sale_send_guard, sale_allowed, template, sell_junk
from test_support import Driver, immediate
from vision_state import Observation


class NavigationGuardTests(unittest.TestCase):
    def test_invalid_coordinates_never_authorize_movement(self):
        for point in (None,(float('nan'),1),(1,float('inf')),(True,2),(101,2)):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as folder:
                driver=Driver()
                with patch('runtime_skills.coordinate',return_value=point),self.assertRaisesRegex(GuardFailed,'invalid_coordinate'):
                    driver.run(location(driver.ctx,folder))
                self.assertEqual(driver.actions,[])

    def test_completed_checkpoint_rechecks_current_location(self):
        driver=Driver()
        driver.ctx.checkpoint['waypoint']=1
        with patch('runtime_skills.location',side_effect=lambda *a:immediate((1,1))):
            result=driver.run(navigate(driver.ctx,'unused',[(2,2)]))
        self.assertEqual(result.reason,'route_obstructed')
        self.assertTrue(driver.actions)

    def test_interrupted_mining_returns_to_junction_before_route_resume(self):
        driver=Driver()
        driver.ctx.checkpoint.update(waypoint=1,mining_return={'position':(1.3,1.3),'deadline':90})
        readings=iter([(1,1),(1.3,1.3),(1.3,1.3),(1.6,1.6)])
        events=[]
        driver.ctx.record=lambda kind,**data:events.append((kind,data))
        with patch('runtime_skills.location',side_effect=lambda *a:immediate(next(readings))):
            result=driver.run(navigate(driver.ctx,'unused',[(.5,.5),(1.6,1.6)]))
        self.assertEqual(result.reason,'ARRIVED')
        self.assertEqual([e['target'] for kind,e in events if kind=='navigation_target'],[(1.3,1.3),(1.6,1.6)])
        self.assertNotIn('mining_return',driver.ctx.checkpoint)
        self.assertNotIn('mining_waypoint',driver.ctx.checkpoint)
        self.assertEqual(driver.ctx.checkpoint['waypoint'],2)
        self.assertEqual(driver.ctx.deadline,100)

    def test_expired_detour_cannot_claim_resumed_navigation(self):
        driver=Driver()
        driver.ctx.checkpoint['mining_return']={'position':(1,1),'deadline':-1}
        result=driver.run(navigate(driver.ctx,'unused',[(2,2)]))
        self.assertEqual(result.reason,'mining_return_budget_expired')
        self.assertEqual(driver.actions,[])

    def test_requested_step_limit_is_preserved_by_legacy_entry(self):
        with patch('runtime_main.main') as entry:
            legacy_entry('navigate_local.py',['--execute','--x','1','--y','2','--max-steps','1','--output','unused'])
        args=parser().parse_args(entry.call_args.args[0])
        self.assertEqual(args.max_steps,1)
        driver=Driver()
        with patch('runtime_skills.location',side_effect=lambda *a:immediate((1,1))):
            result=driver.run(navigate(driver.ctx,'unused',[(2,2)],max_steps=args.max_steps))
        self.assertEqual(result.reason,'navigation_step_limit')
        self.assertEqual(len(driver.actions),2)


class SaleGuardTests(unittest.TestCase):
    def test_same_icon_different_tooltip_is_not_the_same_item(self):
        first=np.zeros((20,20,3),np.uint8)
        second=first.copy()
        second[10,10]=255
        self.assertFalse(sale_frame_unchanged(first,second,{'roi':(0,0,5,5)}, {'item_tooltip_roi':(10,10,5,5)}))

    def test_changed_tooltip_after_money_read_prevents_sale(self):
        driver=Driver()
        before=np.zeros((20,20,3),np.uint8)
        after=before.copy();after[10,10]=255
        driver.frame_image=before
        pointer=[1000,600]
        spec=dict(name='杂货商',window_template='window',window_roi=(0,0,1,1),money_roi=(1,0,1,1),
                  item_tooltip_roi=(10,10,5,5),merchant_points=[(1000,600)],
                  slots=[dict(roi=(0,0,5,5),point=(1500,700),empty_template='empty')],
                  allowed_junk=[dict(name='断牙',category='junk',quality='poor',approved=True,icon='tooth')])
        count=[0]
        def money(*args):
            count[0]+=1
            if count[0]==2:driver.frame_image=after
            return immediate(10)
        def move(ctx,vision,x,y,**kw):
            pointer[:]=[x,y]
            return immediate((driver.frame(),{}))
        def ocr(frame,roi,folder,label):
            return {'items':[dict(text='杂货商' if label.startswith('vendor') else '断牙',confidence=1)]}
        cursor=SimpleNamespace(cursor=lambda _:dict(x=pointer[0],y=pointer[1]))
        with patch('runtime_interactions.LootVision',return_value=cursor),\
             patch('runtime_interactions.move_pointer',side_effect=move),\
             patch('runtime_interactions.read_money',side_effect=money),\
             patch('runtime_interactions.ocr',side_effect=ocr),\
             patch('runtime_interactions.template',return_value={'score':1}):
            result=driver.run(sell_junk(driver.ctx,spec,'unused'))
        self.assertEqual(result.reason,'sale_identity_or_pointer_changed')
        self.assertEqual([a.reason for a in driver.actions],['open_merchant'])

    def test_nan_threshold_rejected_before_asset_lookup(self):
        with self.assertRaisesRegex(ValueError,'threshold'):
            template(None,'not-present',None,float('nan'))

    def test_sale_write_guard_rejects_queued_pointer_change(self):
        driver=Driver()
        driver.frame_image=np.zeros((1080,1920,3),np.uint8)
        authorized=driver.frame()
        slot=dict(roi=(1450,650,100,100),point=(1500,700))
        spec=dict(item_tooltip_roi=(900,300,200,300),window_roi=(400,200,400,400))
        self.assertTrue(sale_send_guard(driver.ctx,authorized,slot,spec))
        driver.frame_image=driver.frame_image.copy()
        driver.frame_image[700,1500]=255
        self.assertFalse(sale_send_guard(driver.ctx,authorized,slot,spec))

    def test_uncertain_tooltip_line_is_retained(self):
        item=dict(name='断牙',category='junk',quality='poor',approved=True)
        tooltip=dict(items=[dict(text='断牙',confidence=1),dict(text='任务物品',confidence=.5)])
        self.assertFalse(sale_allowed(item,tooltip))

    def test_failed_dialog_closure_does_not_claim_safe_return(self):
        driver=Driver()
        with patch('runtime_interactions.template',return_value={'score':1}):
            result=driver.run(close_merchant(driver.ctx,dict(window_template='unused',window_roi=(0,0,1,1))))
        self.assertEqual(result.reason,'merchant_closure_unconfirmed')
        self.assertEqual([a.reason for a in driver.actions],['close_merchant'])


class CompositionGuardTests(unittest.TestCase):
    def test_gather_stops_on_failed_skin_and_does_not_start_mining(self):
        args=parser().parse_args(['--execute','--task','gather','--skinning','--mining','--output','unused'])
        ctl=Controller(args,SimpleNamespace(),SimpleNamespace(folder=Path('unused'),status=lambda **kw:None),{})
        with patch.object(ctl,'run_skill',return_value=Result('failed','receipt_missing')) as run:
            result=ctl.run()
        self.assertEqual(result.status,'failed')
        self.assertEqual(run.call_count,1)
        self.assertEqual(result.facts['skinning']['reason'],'receipt_missing')

    def test_empty_gather_does_not_claim_business_completion(self):
        args=parser().parse_args(['--execute','--task','gather','--output','unused'])
        ctl=Controller(args,SimpleNamespace(),SimpleNamespace(folder=Path('unused'),status=lambda **kw:None),{})
        self.assertEqual(ctl.run().status,'skipped')

    def test_fishing_character_qualification_is_required_even_for_trial(self):
        spec=dict(cast_key=31,world_roi=[1,1,5,5],bobber_template='unused',receipt_items=['鱼'])
        with self.assertRaisesRegex(ValueError,'skill_confirmed'):
            check_profile(spec,'fishing')

    def test_new_target_does_not_inherit_previous_engagement(self):
        driver=Driver()
        snapshots=iter([
            ('first',1,False),('first',.8,False),
            ('second',.9,False),(None,0,True),
        ])
        def frame():
            # Continue observing the peaceful scene through the post-kill guard.
            track,hp,xp=next(snapshots,(None,0,False))
            driver.sequence+=1
            obs=Observation(valid=True,player_hp=1,player_mana=1,target=track is not None,
                            target_allowed=True,target_hp=hp,xp_visible=xp)
            return replace(driver.frame_template,sequence=driver.sequence,captured_at=driver.now,
                           observation=obs,target_track=track)
        driver.frame_template=driver.frame()
        driver.frame=frame
        engagements=[]
        scheduler=SimpleNamespace(note_engagement=lambda s:engagements.append(s.target_track))
        result=driver.run(combat(driver.ctx,scheduler))
        self.assertEqual(result.status,'completed')
        self.assertEqual(engagements,['first'])
        self.assertGreaterEqual(driver.now,3)


if __name__=='__main__':unittest.main()
