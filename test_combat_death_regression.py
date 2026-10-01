import unittest
from types import SimpleNamespace
from unittest.mock import patch
from control_policy import Policy
from vision_state import Observation
from runtime_skills import combat
from runtime_types import Result
from test_support import Driver, make_controller

class DeathRegressionTests(unittest.TestCase):
 def test_late_deaggro_gets_full_quiet_period(self):
  class LatePeace(Driver):
   def frame(self):
    self.observation.in_combat=self.now<2.5
    return super().frame()
  d=LatePeace();p=Policy();p.xp_events=1
  scheduler=SimpleNamespace(combat_options={},note_engagement=lambda _:None)
  with patch('runtime_skills.Policy',return_value=p):result=d.run(combat(d.ctx,scheduler))
  self.assertEqual(result.reason,'xp_limit_out_of_combat')
  self.assertGreaterEqual(d.now,5.5)
  self.assertLess(d.now,8)
  self.assertEqual([a.reason for a in d.actions],['cancel_movement_after_kill'])
 def policy(self):
  p=Policy(attack_once=True,interact_key=65)
  p.attack_started=True;p.damaged_target=True;p.last_progress=36.7
  return p
 def obs(self,**kw):
  data=dict(valid=True,player_hp=.54,player_mana=1,target=True,target_allowed=True,target_hp=.1,in_combat=True)
  data.update(kw);return Observation(**data)
 def test_recorded_low_health_target_gets_one_bounded_swing(self):
  p=self.policy()
  self.assertTrue(p.can_finish_melee(self.obs(),37.96))
  p.last_progress=40
  self.assertTrue(p.can_finish_melee(self.obs(),41))
  self.assertFalse(p.can_finish_melee(self.obs(),42))
 def test_finish_never_applies_to_unsafe_or_non_melee_state(self):
  for change in [dict(player_hp=.39),dict(target_hp=.4),dict(target_allowed=False),dict(target=False)]:
   self.assertFalse(self.policy().can_finish_melee(self.obs(**change),37.96))
  p=self.policy();p.attack_once=False
  self.assertFalse(p.can_finish_melee(self.obs(),37.96))
  self.assertFalse(self.policy().can_finish_melee(self.obs(),41))
 def test_xp_limit_with_attack_ring_does_not_tab_pull(self):
  d=Driver();d.observation.in_combat=True;p=Policy();p.xp_events=1
  scheduler=SimpleNamespace(combat_options={},note_engagement=lambda _:None)
  with patch('runtime_skills.Policy',return_value=p):result=d.run(combat(d.ctx,scheduler))
  self.assertEqual(result.reason,'post_kill_combat_unresolved')
  self.assertEqual(result.facts['xp_events'],1)
  self.assertEqual([a.reason for a in d.actions],['cancel_movement_after_kill'])
 def test_kill_frame_itself_cannot_send_a_tab(self):
  d=Driver();d.observation.in_combat=True;d.observation.xp_visible=True
  p=Policy();p.pending_xp_until=100
  scheduler=SimpleNamespace(combat_options={},note_engagement=lambda _:None)
  with patch('runtime_skills.Policy',return_value=p):result=d.run(combat(d.ctx,scheduler))
  self.assertEqual(result.facts['xp_events'],1)
  self.assertNotIn(43,[a.values[0] for a in d.actions])
  self.assertNotIn(41,[a.values[0] for a in d.actions])
 def test_failed_retreat_preserves_earlier_kill(self):
  c=make_controller(['--no-loot','--kills','1'])
  c.s.source=SimpleNamespace(peek=lambda:SimpleNamespace(observation=self.obs()))
  def run(name,*args,**kw):
   if name=='ready':return Result('completed','ready')
   return Result('failed','combat_health_retreat',{'xp_events':1}) if name=='combat' else Result('failed','escape_failed')
  with patch.object(c,'run_skill',side_effect=run):result=c.run()
  self.assertEqual(c.kills,1)
  self.assertEqual(result.facts['confirmed_kills'],1)
  self.assertEqual(result.facts['combat_result']['facts']['xp_events'],1)
 def test_peaceful_xp_frame_still_requires_quiet_period(self):
  d=Driver();d.observation.xp_visible=True
  p=Policy();p.pending_xp_until=100
  scheduler=SimpleNamespace(combat_options={},note_engagement=lambda _:None)
  with patch('runtime_skills.Policy',return_value=p):result=d.run(combat(d.ctx,scheduler))
  self.assertEqual(result.reason,'xp_limit_out_of_combat')
  self.assertGreaterEqual(d.now,3)
  self.assertEqual(result.facts['xp_events'],1)
  self.assertNotIn(43,[a.values[0] for a in d.actions])
if __name__=='__main__':unittest.main()
