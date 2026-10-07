import copy
import unittest
from pathlib import Path
import cv2
import numpy as np
from unittest.mock import patch
from class_profiles import load_class, policy_options
from control_policy import Policy
from vision_state import Observation, Vision
from runtime_main import parser, preflight
from runtime_world import load_profile, ROOT

class ClassProfilesTest(unittest.TestCase):
    def test_paladin_experience_bar_detects_confirmed_kill_growth(self):
        vision=Vision('classes/paladin/vision/profile.json')
        fixture=Path('tests/fixtures/paladin')
        values=[]
        for label in ('before','after'):
            patch=cv2.imread(str(fixture/f'experience-{label}.png'))
            frame=np.zeros((1080,1920,3),dtype=np.uint8)
            frame[889:898,486:1575]=patch
            values.append(vision.bar(frame,'experience'))
        self.assertAlmostEqual(values[0],.8264,places=3)
        self.assertAlmostEqual(values[1],.8558,places=3)
        self.assertTrue(.004<values[1]-values[0]<.15)

    def test_paladin_level_up_wrap_requires_calibrated_level_rise(self):
        vision=Vision('classes/paladin/vision/profile.json')
        fixture=Path('tests/fixtures/paladin')
        def frame(stage,level_stage=None):
            image=np.zeros((1080,1920,3),dtype=np.uint8)
            image[889:898,486:1575]=cv2.imread(str(fixture/f'levelup-{stage}-xp.png'))
            image[206:223,1074:1093]=cv2.imread(str(fixture/f'levelup-{level_stage or stage}-level.png'))
            return image
        self.assertFalse(vision.experience_event(frame('before')))
        self.assertFalse(vision.experience_event(frame('after','before')))
        self.assertTrue(vision.experience_event(frame('after')))
        self.assertFalse(vision.experience_event(frame('after')))

    def test_warlock_legacy_attack_preserved(self):
        obs=Observation(valid=True,player_hp=1,player_mana=1,target=True,target_hp=1,target_allowed=True)
        original=Policy(skip_unknown=True)
        archived=Policy(skip_unknown=True,**policy_options(load_class('warlock')))
        self.assertEqual(original.step(obs,1,0),archived.step(obs,1,0))
        self.assertEqual(archived.ready_at,3.6)

    def test_paladin_cannot_inherit_attack_even_in_trial(self):
        unverified=copy.deepcopy(load_class('paladin'))
        unverified['vision_verified']=False
        with self.assertRaisesRegex(ValueError,'calibration required'):
            policy_options(unverified)
        a=parser().parse_args(['--class','paladin','--task','combat','--trial','--output','unused'])
        a.profile=ROOT/load_class('paladin')['vision_profile']
        with patch('class_profiles.load_class',return_value=unverified):
            checks=preflight(a,load_profile())
        self.assertFalse(checks['ready'])
        self.assertTrue(any('paladin' in issue for issue in checks['issues']))

    def test_class_attack_settings_are_used(self):
        spec=copy.deepcopy(load_class('warlock'))
        spec['policy'].update(attack_key=30,attack_interval=1.5)
        policy=Policy(**policy_options(spec))
        obs=Observation(valid=True,player_hp=1,player_mana=1,target=True,target_hp=1,target_allowed=True)
        self.assertEqual(policy.step(obs,1,0).key,30)
        self.assertEqual(policy.ready_at,2.5)

    def test_bad_settings_rejected(self):
        for key,value in [('attack_key',None),('attack_key',True),('attack_interval',float('nan')),('mana_rest_at',.99)]:
            spec=copy.deepcopy(load_class('warlock')); spec['policy'][key]=value
            with self.assertRaises(ValueError): policy_options(spec)

if __name__=='__main__': unittest.main()

class PaladinSequenceTest(unittest.TestCase):
    def policy(self):
        return Policy(attack_key=30,attack_interval=1.6,attack_once=True,precombat_key=31)

    def obs(self,**kwargs):
        values=dict(valid=True,player_hp=1,player_mana=1,target=True,target_hp=1,target_allowed=True,bearing=0,maintenance_buff_present=True)
        values.update(kwargs)
        return Observation(**values)

    def test_seal_then_attack_not_repeated_toggle(self):
        p=self.policy()
        self.assertEqual(p.step(self.obs(),0,0).key,31)
        self.assertIsNone(p.step(self.obs(),1,0))
        self.assertEqual(p.step(self.obs(),1.7,0).key,30)
        for t,hp in [(3.5,.8),(5.5,.6),(7.5,.4)]:
            self.assertIsNone(p.step(self.obs(target_hp=hp,in_combat=True),t,0))
        self.assertIsNone(p.stopped)

    def test_approach_does_not_toggle_attack_off(self):
        p=self.policy(); p.step(self.obs(),0,0); p.step(self.obs(),1.7,0)
        self.assertEqual(p.step(self.obs(),5.3,0).reason,'approach_target')
        self.assertIsNone(p.step(self.obs(),5.6,0))
        self.assertEqual(p.step(self.obs(),6.1,0).reason,'approach_target')
        self.assertEqual(p.step(self.obs(),10,0).reason,'approach_target')

    def test_melee_approaches_already_injured_target_at_short_intervals(self):
        p=self.policy(); o=self.obs(target_hp=.7)
        self.assertEqual(p.step(o,0,0).reason,'precombat_buff')
        self.assertEqual(p.step(o,1.7,0).reason,'attack')
        for t in [2,2.7,3.4]:
            self.assertEqual(p.step(o,t,0).reason,'approach_target')
        self.assertIsNone(p.step(self.obs(target_hp=.5),4.1,0))

    def test_melee_reacquires_without_blind_forward_movement(self):
        p=self.policy(); p.step(self.obs(),0,0)
        self.assertEqual(p.step(self.obs(bearing=None),1.7,0).reason,'melee_reacquire_target')
        self.assertEqual(p.step(self.obs(bearing=200),2.1,0).reason,'face_target')

    def test_new_encounter_gets_new_seal(self):
        p=self.policy(); p.step(self.obs(),0,0); p.step(self.obs(),1.7,0)
        p.step(self.obs(target=False),3.5,0)
        p.step(self.obs(target=False),4.2,0)
        self.assertEqual(p.step(self.obs(),5,0).reason,'precombat_buff')

    def test_brief_missing_target_does_not_restart_attack(self):
        p=self.policy(); p.step(self.obs(),0,0); p.step(self.obs(),1.7,0)
        self.assertIsNone(p.step(self.obs(target=False),2,0))
        self.assertIsNone(p.step(self.obs(target_hp=.8,in_combat=True),3.5,0))
        self.assertTrue(p.attack_started)

    def test_experience_bar_changes_once_and_nameplate_is_centered(self):
        import cv2
        from vision_state import Vision
        v=Vision(ROOT/'classes/paladin/vision/profile-before-relocation.json')
        before=cv2.imread(str(ROOT/'tests/fixtures/paladin/before.jpg'))
        after=cv2.imread(str(ROOT/'tests/fixtures/paladin/after-first.jpg'))
        self.assertAlmostEqual(v.bearing(before),37.5)
        self.assertFalse(v.observe(before).xp_visible)
        self.assertTrue(v.observe(after).xp_visible)
        self.assertFalse(v.observe(after).xp_visible)
        self.assertFalse(v.observe(before).xp_visible)

    def test_unknown_target_and_combat_do_not_get_prebuff(self):
        p=self.policy()
        self.assertIsNone(p.step(self.obs(target_allowed=False),0,0))
        self.assertEqual(p.step(self.obs(in_combat=True),.1,0).key,30)

class ClickToMoveTest(unittest.TestCase):
    obs = PaladinSequenceTest.obs
    def test_ctm_uses_interaction_once_without_bearing_or_w(self):
        options=policy_options(load_class('paladin'));options['opener_key']=None
        p=Policy(**options)
        o=self.obs(bearing=None)
        self.assertEqual(p.step(o,0,0).key,31)
        self.assertEqual(p.step(o,1.7,0).key,65)
        for t in [2,3,5,8]: self.assertIsNone(p.step(o,t,0))
        self.assertIsNone(p.step(o,12,0))
        self.assertEqual(p.stopped,'click_to_move_no_damage')

    def test_ctm_target_loss_cancels_before_search(self):
        options=policy_options(load_class('paladin'));options['opener_key']=None
        p=Policy(**options)
        p.step(self.obs(),0,0); p.step(self.obs(),1.7,0); p.step(self.obs(),1.9,0)
        self.assertIsNone(p.step(self.obs(target=False),2,0))
        self.assertEqual(p.step(self.obs(target=False),2.7,0).reason,'cancel_click_to_move')
        self.assertEqual(p.step(self.obs(target=False),3,0).reason,'select_or_scan')

    def test_ctm_dead_or_unknown_target_never_interacted(self):
        for o in [self.obs(target_hp=0),self.obs(target_allowed=False)]:
            options=policy_options(load_class('paladin'));options['opener_key']=None
            p=Policy(**options)
            for t in [0,1,2,3]:
                action=p.step(o,t,0)
                self.assertTrue(action is None or action.key!=65)

    def test_executor_revocation_and_close_cancel_ctm(self):
        from runtime_engine import Executor
        from unittest.mock import Mock
        b=Mock(); e=Executor(b,Mock(),Mock())
        e.ctm_pending=True
        e.revoke(); e.close()
        b.tap.assert_called_once_with(22,20)
        b.close.assert_called_once()

class PaladinBagTest(unittest.TestCase):
    def test_combined_bag_matches_observed_fifteen_empty_slots(self):
        import cv2,json
        from interaction_vision import BagVision
        layout=json.loads((ROOT/'classes/paladin/vision/profile-before-relocation.json').read_text())['backpack']
        v=BagVision(layout)
        reading=v.observe(cv2.imread(str(ROOT/'tests/fixtures/paladin/bag-open.jpg')))
        self.assertTrue(reading['valid'])
        self.assertEqual((reading['empty'],reading['occupied']),(15,5))
        self.assertFalse(v.observe(cv2.imread(str(ROOT/'tests/fixtures/paladin/bag-closed.jpg')))['open'])

class PaladinInteractionCalibrationTest(unittest.TestCase):
    def test_current_scaled_loot_cursor_and_new_receipt(self):
        import cv2,json
        from interaction_vision import LootVision
        p=json.loads((ROOT/'classes/paladin/vision/profile-before-relocation.json').read_text())
        v=LootVision(p['cursor_templates'],p['loot_message_template'])
        before=cv2.imread(str(ROOT/'tests/fixtures/paladin/loot-before.png'))
        after=cv2.imread(str(ROOT/'tests/fixtures/paladin/loot-after.jpg'))
        self.assertEqual(v.cursor(before)['kind'],'loot')
        self.assertEqual(v.loot_messages(before),0)
        self.assertEqual(v.loot_messages(after),1)
        v=LootVision(p['cursor_templates'])
        self.assertEqual(v.cursor(cv2.imread(str(ROOT/'tests/fixtures/paladin/bag-closed.jpg')))['kind'],'hand')
