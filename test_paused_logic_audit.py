from test_support import requires_archive
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from combat_exit_watch import ExitWatch
from combat_log_cv import evidence,joined_lines
from vision_state import Observation
from runtime_main import Controller
from runtime_engine import Result

class PausedAuditTests(unittest.TestCase):
 def obs(self,hp=.9):return Observation(valid=True,player_hp=hp,in_combat=False,target=False)
 def test_old_injury_can_settle(self):
  w=ExitWatch(0)
  w.step(self.obs(1),True,.1,0,sequence=0)
  self.assertEqual(w.step(self.obs(.95),True,.1,1,sequence=1),'observe')
  for t in (2,3,4):self.assertEqual(w.step(self.obs(.95),True,.1,t,sequence=t),'observe')
  self.assertEqual(w.step(self.obs(.95),True,.1,5,sequence=5),'peace_confirmed')
 def test_duplicate_frame_cannot_confirm_peace(self):
  w=ExitWatch(0)
  for t in (0,1,3,8):self.assertEqual(w.step(self.obs(),True,.1,t,sequence=1),'observe')
 def test_incoming_text_alone_never_moves(self):
  w=ExitWatch(0)
  for t in (0,1,8,20):self.assertEqual(w.step(self.obs(),True,.1,t,sequence=t,recent_incoming=True),'observe')
 def test_death_and_damage_text(self):
  self.assertEqual(evidence(['［你已死亡。］'])[0]['kind'],'death_text')
  self.assertEqual(evidence(['狗头人苦力的攻击命中你，造成5点伤害。'])[0]['kind'],'incoming_damage_text')
  self.assertEqual(evidence(['狗头人苦力已死亡。']),[])
 def test_fragments_join_without_merging_adjacent_rows(self):
  items=[dict(text=t,confidence=.9,box=[x,y,.2,.04]) for t,x,y in [('造成5点伤害',.7,.8),('狗头人苦力',.1,.8),('的攻击命中你',.4,.8),('你杀死了狗头人劳工',.1,.7)]]
  self.assertEqual([x['kind'] for x in evidence(joined_lines(items))],['incoming_damage_text','kill_text'])
 def controller(self,stopped=False):
  c=Controller.__new__(Controller);c.a=SimpleNamespace(task='patrol');c.store=Mock();c.kills=0
  c.s=Mock();c.s.source=SimpleNamespace(death_review_pending=False,peek=Mock(side_effect=RuntimeError('offline')))
  c.s.stop_event.is_set.return_value=stopped
  import time
  c.s.clock=time.monotonic;c.s._stopped=lambda:stopped;c.s.store=c.store
  return c
 def test_explicit_stop_never_runs_cleanup_input(self):
  c=self.controller(True);r=Result('cancelled','user_stopped')
  self.assertIs(c.ensure_safe_exit(r),r);c.s.run.assert_not_called();c.s.source.peek.assert_not_called()
 def test_failure_after_stop_does_not_observe_or_escape(self):
  c=self.controller(True);r=c.ensure_safe_exit(Result('failed','recognizer_failed'))
  self.assertEqual(r.facts['safety_exit'],'user_stopped');c.s.run.assert_not_called();c.s.source.peek.assert_not_called()
 def test_already_settled_failure_cannot_repeat_escape(self):
  c=self.controller();r=Result('failed','recovery_failed',{'safety_exit':'threat_unresolved'})
  self.assertIs(c.ensure_safe_exit(r),r);c.s.run.assert_not_called();c.s.source.peek.assert_not_called()
 def test_capture_failure_is_not_safe(self):
  c=self.controller();r=c.ensure_safe_exit(Result('failed','invalid_or_stale_perception'))
  self.assertEqual(r.facts['safety_exit'],'observation_unavailable');self.assertTrue(r.facts['manual_attention_required']);c.s.run.assert_not_called()
 def test_deadline_also_checks_unresolved_combat(self):
  c=self.controller();r=c.ensure_safe_exit(Result('cancelled','run_deadline'))
  c.s.source.peek.assert_called_once();self.assertTrue(r.facts['manual_attention_required'])

 @requires_archive('runs/death33-incoming-ocr.json')
 def test_recorded_fatal_log(self):
  import json
  from pathlib import Path
  rows=evidence(joined_lines(json.loads(Path('runs/death33-incoming-ocr.json').read_text())['items']))
  self.assertIn('death_text',[r['kind'] for r in rows])
  self.assertIn('incoming_damage_text',[r['kind'] for r in rows])
