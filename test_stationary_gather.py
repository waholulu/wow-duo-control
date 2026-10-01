"""The first gathering mode must never turn a weak observation into movement."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np

from runtime_main import parser, validate, preflight
from runtime_world import load_profile
from stationary_gather import gather_once, world_candidates, confirmed_tooltip, validate_stationary_profile
from run_permission import input_effect_issues
from test_support import Driver


class StationaryGatherTests(unittest.TestCase):
    def setUp(self):
        self.driver=Driver()
        self.item=dict(names=['铜矿脉'],receipt_items=['铜矿石'],
                       tooltip_roi=[100,100,50,50],receipt_roi=[200,200,50,50],
                       cursor_template='unused',cursor_kind='mine')
        self.spec={'resources':{'mining':self.item},'settings':dict(
            minimum_health=.95,minimum_empty_slots=1,candidate_observations=2,
            candidate_stability_px=6,candidate_separation_px=16,
            tooltip_confidence=.8,receipt_confidence=.8,receipt_wait_seconds=12)}
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def run_skill(self, points, tooltip=True, receipt=False, resource='mining'):
        item=self.spec['resources'][resource]
        def pointer(ctx, vision, x, y, **kwargs):
            snapshot=yield from ctx.observe()
            return snapshot,dict(kind=item['cursor_kind'],x=x,y=y)
        with (patch('stationary_gather.select_chat',side_effect=lambda *args: _done_chat()),
              patch('stationary_gather.world_candidates',side_effect=points),
              patch('stationary_gather.move_pointer',side_effect=pointer),
              patch('stationary_gather.LootVision'),
              patch('stationary_gather.ocr',return_value={'items':[dict(text=item['names'][0],confidence=1)]} if tooltip else {'items':[]}),
              patch('stationary_gather.read_receipts',side_effect=lambda *args: _value({})),
              patch('stationary_gather.receipt_confirmation',side_effect=lambda *args: _value(receipt))):
            return self.driver.run(gather_once(self.driver.ctx,self.spec,resource,Path(self.folder.name)))

    def test_unknown_or_ambiguous_node_never_clicks(self):
        for candidates in ([],[(500,500),(650,500)]):
            with self.subTest(candidates=candidates):
                self.driver.actions.clear()
                result=self.run_skill([candidates])
                self.assertEqual(result.status,'skipped')
                self.assertFalse(self.driver.actions)

    def test_bad_name_never_clicks(self):
        result=self.run_skill([[(500,500)],[(501,501)]],tooltip=False)
        self.assertEqual(result.reason,'gather_name_unconfirmed')
        self.assertFalse(self.driver.actions)

    def test_configured_observation_count_and_tolerance_apply(self):
        self.spec['settings']['candidate_observations']=3
        self.spec['settings']['candidate_stability_px']=2
        result=self.run_skill([[(500,500)],[(501,501)],[(505,501)]])
        self.assertEqual(result.reason,'gather_world_node_unstable')
        self.assertFalse(self.driver.actions)

    def test_missing_receipt_keeps_one_click_uncertain(self):
        result=self.run_skill([[(500,500)],[(501,501)]])
        self.assertEqual(result.reason,'gather_receipt_unconfirmed')
        self.assertEqual([a.kind for a in self.driver.actions],['click'])
        self.assertTrue(self.driver.ctx.checkpoint['uncertain_transaction'])

    def test_receipt_confirms_one_click(self):
        result=self.run_skill([[(500,500)],[(501,501)]],receipt=True)
        self.assertEqual(result.reason,'gather_personal_receipt_confirmed')
        self.assertEqual([a.kind for a in self.driver.actions],['click'])
        self.assertFalse(self.driver.ctx.checkpoint['uncertain_transaction'])

    def test_herbalism_uses_same_flow_and_its_own_configuration(self):
        self.spec['resources']['herbalism']=dict(self.item,cursor_kind='herb',
                                                  names=['宁神花'],receipt_items=['宁神花'])
        result=self.run_skill([[(500,500)],[(501,501)]],receipt=True,resource='herbalism')
        self.assertEqual(result.facts['resource'],'herbalism')
        self.assertEqual([a.reason for a in self.driver.actions],['gather_herbalism'])

    def test_unverified_configuration_and_missing_operator_flag_block_start(self):
        a=parser().parse_args(['--task','gather-station','--resource','mining','--execute',
                               '--trial','--max-seconds','60','--output','unused'])
        with self.assertRaisesRegex(ValueError,'station-confirmed'):
            validate(a)
        a.station_confirmed=True
        self.assertFalse(preflight(a,load_profile())['ready'])

    def test_two_real_template_peaks_are_ambiguous(self):
        rng=np.random.default_rng(42)
        image=rng.integers(20,220,(12,12,3),dtype=np.uint8)
        frame=np.zeros((1080,1920,3),dtype=np.uint8)
        frame[350:362,700:712]=image
        frame[350:362,800:812]=image
        item={'world_roi':[650,300,220,110],
              'node_templates':[{'file':'unused','threshold':.95}]}
        with patch('stationary_gather.cv2.imread',return_value=image):
            points=world_candidates(frame,item)
        self.assertEqual(len(points),2)

    def test_tooltip_requires_exact_high_confidence_name(self):
        self.assertFalse(confirmed_tooltip({'items':[dict(text='铜矿脉附近',confidence=1)]},['铜矿脉']))
        self.assertFalse(confirmed_tooltip({'items':[dict(text='铜矿脉',confidence=.6)]},['铜矿脉']))
        self.assertTrue(confirmed_tooltip({'items':[dict(text='铜矿脉',confidence=.95)]},['铜矿脉']))
        self.assertFalse(confirmed_tooltip({'items':[dict(text='铜矿脉',confidence=.85)]},['铜矿脉'],.9))

    def test_skill_confirmation_is_character_specific(self):
        image=np.random.default_rng(7).integers(30,210,(12,12,3),dtype=np.uint8)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cv2.imwrite(str(root/'cursor.png'),image)
            cv2.imwrite(str(root/'node.png'),image)
            item=dict(self.item,calibrated=True,requires_tool=True,
                      world_roi=[450,300,120,120],cursor_template='cursor.png',
                      node_templates=[dict(file='node.png',threshold=.95)])
            spec=dict(self.spec,stationary_interaction_verified=True,
                      resources={'mining':item},
                      characters={'paladin':{'mining':{'skill_confirmed':True,'tool_confirmed':True}},
                                  'warlock':{'mining':{'skill_confirmed':False,'tool_confirmed':False}}})
            with patch('stationary_gather.ROOT',root):
                self.assertIs(validate_stationary_profile(spec,'mining','paladin'),item)
                with self.assertRaisesRegex(ValueError,'warlock mining skill_confirmed'):
                    validate_stationary_profile(spec,'mining','warlock')

    def test_current_input_hold_blocks_new_gathering(self):
        from runtime_world import ROOT
        self.assertIn('pc_input_effect_unconfirmed',input_effect_issues(ROOT,'paladin'))


def _value(value):
    if False:yield
    return value


def _done_chat():
    from runtime_types import Result
    if False:yield
    return Result('completed','chat_panel_visible')
