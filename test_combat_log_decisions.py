from test_support import requires_archive
import unittest,json
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from types import SimpleNamespace
from combat_log_cv import CombatLogReader,evidence,loss_decision,read_lines
from test_support import Driver
from runtime_skills import combat
from vision_state import Observation
from control_action import Action
class CombatLogDecisionTests(unittest.TestCase):
 @requires_archive('runs/paladin-convergence-live-06/evidence/*/00000192.jpg')
 def test_transparent_combat_panel_accepts_live_background_variation(self):
  import cv2
  from chat_tabs import selected
  root=Path(__file__).resolve().parent
  profile=json.loads((root/'classes/paladin/vision/profile.json').read_text())
  frame=cv2.imread(str(next((root/'runs/paladin-convergence-live-06/evidence').glob('*/00000192.jpg'))))
  self.assertTrue(selected(frame,profile['chat_tabs']['combat']))

 @requires_archive('runs/paladin-convergence-live-04/evidence/*/00000067.jpg',
                   'runs/paladin-convergence-live-10-preflight-frame.jpg',
                   'runs/paladin-convergence-live-10/evidence/*/00000313.jpg')
 def test_combat_filter_accepts_low_contrast_live_frames_but_not_inactive_copy(self):
  import cv2,json
  from chat_tabs import selected
  root=Path(__file__).resolve().parent
  profile=json.loads((root/'classes/paladin/vision/profile.json').read_text())
  spec=profile['chat_tabs']['combat']
  paths=[next((root/'runs/paladin-convergence-live-04/evidence').glob('*/00000067.jpg')),
         root/'runs/paladin-convergence-live-10-preflight-frame.jpg',
         next((root/'runs/paladin-convergence-live-10/evidence').glob('*/00000313.jpg'))]
  paths+=list((root/'runs/paladin-convergence-live-10-postfix-safety-02/evidence').glob('*/*.jpg'))
  for path in paths:
   with self.subTest(frame=path.name):self.assertTrue(selected(cv2.imread(str(path)),spec))
  inactive=cv2.imread(str(paths[-1]));x,y,w,h=spec['active_contrast']['rois'][0]
  inactive[y:y+h,x:x+w]=0
  self.assertFalse(selected(inactive,spec))

 @requires_archive('runs/paladin-convergence-live-01/evidence/1790700467629470000/00000106.jpg')
 def test_relocated_live_log_keeps_personal_kill_prefix(self):
  import cv2
  root=Path(__file__).resolve().parent
  profile=json.loads((root/'classes/paladin/vision/profile.json').read_text())
  frame=cv2.imread(str(root/'runs/paladin-convergence-live-01/evidence/1790700467629470000/00000106.jpg'))
  x,y,w,h=profile['combat_log_roi'];rows=evidence(read_lines(frame[y:y+h,x:x+w]))
  self.assertTrue(any(row['kind']=='kill_text' for row in rows))
  self.assertTrue(any(row['kind']=='damage_text' for row in rows))

 @requires_archive('runs/paladin-cv-twenty-17/events.jsonl')
 def test_recorded_damage_can_hold_but_other_names_and_stale_cannot(self):
  es=[json.loads(l) for l in Path('runs/paladin-cv-twenty-17/events.jsonl').read_text().splitlines()]
  lines=next(e['lines'] for e in es if e['kind']=='combat_log_ocr' and e.get('frame')==80)
  rows=evidence(lines);self.assertTrue(any(e['kind']=='damage_text' for e in rows))
  r=CombatLogReader([0,0,1,1],lambda *a,**kw:None)
  try:
   r.recent=[dict(x,captured_at=11,frame=80) for x in rows]
   recent=r.latest(12,10,['狗头人歹徒'])
   self.assertEqual(loss_decision(recent,True,True,1),'wait_for_target_reacquisition')
   self.assertEqual(r.latest(12,10,['森林狼']),[])
   self.assertEqual(r.latest(15,10,['狗头人歹徒']),[])
   self.assertEqual(r.latest(12,11.5,['狗头人歹徒']),[])
   self.assertIsNone(loss_decision(recent,False,True,1))
   self.assertEqual(loss_decision(recent,True,True,4),'combat_log_target_unresolved')
  finally:r.close()
 def test_death_stops_and_kill_waits_for_independent_confirmation(self):
  self.assertEqual(loss_decision([dict(kind='death_text')],True,False,0),'combat_log_death_review')
  self.assertEqual(loss_decision([dict(kind='kill_text')],True,False,1),'wait_for_kill_confirmation')
  self.assertEqual(loss_decision([dict(kind='kill_text')],True,False,4),'combat_log_kill_unconfirmed')
 def test_actual_combat_skill_waits_without_movement_then_stops_for_review(self):
  d=Driver();d.ctx.vision_profile={'combat_log_target_names':{'kobold':['狗头人歹徒']}}
  base=d.frame
  def frame():
   s=base();present=not d.actions
   o=Observation(valid=True,player_hp=1,player_mana=1,target=present,target_allowed=present,target_hp=1 if present else 0,bearing=0,in_combat=not present,opener_in_range=True)
   return replace(s,observation=o,target_track='1' if present else None,target_label='kobold' if present else None)
  d.frame=frame;d.ctx.current_snapshot=frame
  calls=[]
  reader=SimpleNamespace(latest=lambda now,since,names:[dict(kind='damage_text',text='你的近战攻击命中狗头人歹徒',captured_at=now)])
  scheduler=SimpleNamespace(source=SimpleNamespace(combat_log=reader),combat_options=dict(attack_once=True,attack_key=30,interact_key=65,opener_key=33),note_engagement=lambda s:None)
  d.ctx.record=lambda kind,**kw:calls.append((kind,kw))
  result=d.run(combat(d.ctx,scheduler))
  self.assertEqual(result.reason,'combat_log_target_unresolved')
  self.assertEqual([a.reason for a in d.actions],['ranged_opener'])
  self.assertTrue(any(k=='combat_log_decision' for k,v in calls))
  self.assertEqual(result.facts['xp_events'],0)

 def test_confirmed_xp_takes_precedence_over_lingering_kill_text(self):
  class ConfirmedPolicy:
   def __init__(self,**kw):
    self.xp_events=0;self.damaged_target=True;self.rest_cycles=0
    self.buff_cast_at=self.opener_last_at=None
    self.stopped=None;self.state='ACTIVE';self.attack_once=False
    self.attack_interval=2.6
   def reset_for_target_change(self,now):pass
   def action_checkpoint(self):return None
   def reject_action(self,checkpoint):pass
   def step(self,observation,*args):
    if observation.target:
     self.xp_events=1
     return Action(31,80,'attack')
    raise AssertionError('finishing must precede target search')
  d=Driver();d.ctx.vision_profile={}
  base=d.frame
  def frame():
   s=base();present=not d.actions
   o=Observation(valid=True,player_hp=1,player_mana=1,target=present,
                 target_allowed=present,target_hp=1 if present else 0,
                 in_combat=present,bearing=0,opener_in_range=True)
   return replace(s,observation=o,target_track='1' if present else None)
  d.frame=frame;d.ctx.current_snapshot=frame
  reader=SimpleNamespace(latest=lambda now,since,names:[
      dict(kind='kill_text',text='你杀死了狗头人歹徒',captured_at=now)])
  scheduler=SimpleNamespace(source=SimpleNamespace(combat_log=reader),combat_options={},
                            buff_cast_at=None,opener_last_at=None,recovery_options={},
                            note_engagement=lambda s:None)
  records=[];d.ctx.record=lambda kind,**kw:records.append((kind,kw))
  with patch('runtime_skills.Policy',ConfirmedPolicy):
   result=d.run(combat(d.ctx,scheduler,xp_limit=1))
  self.assertEqual((result.status,result.reason),('completed','xp_limit_out_of_combat'))
  self.assertEqual(result.facts['xp_events'],1)
  self.assertTrue(any(k=='combat_log_confirmation_reconciled' for k,v in records))
