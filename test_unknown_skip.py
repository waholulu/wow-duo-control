import unittest
from control_policy import Policy
from vision_state import Observation
class UnknownSkipTests(unittest.TestCase):
 def obs(self,**changes):
  o=Observation(valid=True,player_hp=1,player_mana=1,target=True,target_hp=1,target_allowed=False)
  for k,v in changes.items():setattr(o,k,v)
  return o
 def test_bounded_selection_only(self):
  p=Policy(skip_unknown=True);o=self.obs()
  actions=[]
  for i in range(100):
   a=p.step(o,i*.2,.1)
   if a:actions.append(a)
  self.assertEqual(len(actions),6)
  self.assertTrue(all(a.key==43 for a in actions));self.assertEqual(p.stopped,'target_not_allowed')
 def test_never_skip_during_combat_or_active_encounter(self):
  for combat,encounter in [(True,None),(False,0)]:
   p=Policy(skip_unknown=True);p.fight_started=encounter;o=self.obs(in_combat=combat)
   self.assertIsNone(p.step(o,0,.1));self.assertIsNone(p.step(o,1,.1))
   self.assertEqual(p.stopped,'target_not_allowed')
if __name__=='__main__':unittest.main()
