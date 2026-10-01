import unittest
from unittest.mock import patch
from control_policy import Policy
from vision_state import Observation,Vision

class RequiredBuffTests(unittest.TestCase):
    def policy(self):
        return Policy(attack_key=30,attack_once=True,precombat_key=31,interact_key=65,maintain_precombat_buff=True)
    def obs(self,**kw):
        d=dict(valid=True,player_hp=1,player_mana=1,target=True,target_allowed=True,target_hp=1);d.update(kw);return Observation(**d)
    def engaged(self):
        p=self.policy();self.assertEqual(p.step(self.obs(),0,0).key,31)
        self.assertIsNone(p.step(self.obs(),1,0))
        self.assertEqual(p.step(self.obs(),1.7,0).key,65);return p
    def test_timer_does_not_require_icon(self):
        for present in (None,False,True):
            p=self.policy();o=self.obs(maintenance_buff_present=present)
            self.assertEqual(p.step(o,0,0).key,31)
            self.assertEqual(p.step(o,1.7,0).key,65)
    def test_refresh_during_combat_without_toggling_attack(self):
        p=self.engaged()
        self.assertIsNone(p.step(self.obs(in_combat=True,target_hp=.8),23.9,0))
        self.assertEqual(p.step(self.obs(in_combat=True,target_hp=.7),24,0).key,31)
        self.assertTrue(p.attack_started)
    def test_delayed_engagement_refreshes_expired_timer_first(self):
        p=self.policy();p.step(self.obs(),0,0)
        self.assertEqual(p.step(self.obs(),35,0).key,31)
        self.assertEqual(p.step(self.obs(),36.7,0).key,65)
    def test_initial_combat_still_gets_buff(self):
        p=self.policy();self.assertEqual(p.step(self.obs(in_combat=True),0,0).key,31)
    def test_no_mana_stops_without_starting_timer(self):
        p=self.policy();self.assertIsNone(p.step(self.obs(player_mana=.1),0,0))
        self.assertEqual(p.stopped,'required_buff_insufficient_mana');self.assertIsNone(p.buff_cast_at)
    def test_target_loss_under_threat_never_tabs(self):
        p=self.engaged();o=self.obs(target=False,in_combat=True)
        p.step(o,3,0);self.assertIsNone(p.step(o,3.7,0))
        a=p.step(o,4.3,0);self.assertEqual(a.reason,'cancel_click_to_move')
        self.assertIsNone(p.step(o,4.4,0));self.assertEqual(p.stopped,'target_lost_under_threat')
    def test_normal_perception_does_not_match_buff_templates(self):
        import cv2
        v=Vision('classes/paladin/vision/profile-before-relocation.json');original=v.matches
        def checked(frame,name):
            self.assertFalse(name.startswith('maintenance_buff'))
            return original(frame,name)
        with patch.object(v,'matches',side_effect=checked):
            self.assertIsNone(v.observe(cv2.imread('runs/seal-check-after.png')).maintenance_buff_present)
    def test_carried_timer_skips_recasting_between_encounters(self):
        p=self.policy();p.buff_cast_at=10
        self.assertEqual(p.step(self.obs(),20,0).key,65)
if __name__=='__main__':unittest.main()
