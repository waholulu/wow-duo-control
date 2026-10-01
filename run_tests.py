"""Offline functional suites. Default: current paladin combat; all is explicit."""
import argparse
import json
from pathlib import Path
import os
import unittest

ROOT=Path(__file__).resolve().parent
SUITES={
 'core': 'test_runtime_engine test_core_lifecycle test_core_serial test_death_review test_controller_ownership'.split(),
 'combat': '''test_class_profiles test_combat_audit_fixes test_combat_death_regression test_combat_escape
 test_combat_exit_watch test_combat_log_decisions test_threat_state
 test_escape_quiet test_judgement_opener test_mana_rest test_mana_target_regression
 test_near_zero_target test_paused_logic_audit test_precombat_retry test_recent_runtime_fixes
 test_recovery_heal test_required_buff test_unknown_skip test_workflow_efficiency
 test_runtime_skills.CompositionTests test_runtime_skills.CombatCompletionPriorityTest test_runtime_skills.FailedPullRecoveryTest
 test_workflow_guards.CompositionGuardTests.test_new_target_does_not_inherit_previous_engagement'''.split(),
 'vision': '''test_bag_vision test_health_occlusion test_loot_vision test_percent_cache
 test_status_text test_target_frame_edge test_target_highlight test_target_tracking
 test_ui_update test_window_calibration'''.split(),
 'loot': '''test_bag_vision test_coin_receipt test_coin_scroll
 test_loot_confirmation test_loot_vision test_skin_receipt test_skin_row_replay
 test_runtime_skills.SkillReplayTests test_workflow_efficiency'''.split(),
 'fishing': '''test_fishing_delays test_fishing_inventory test_fishing_parameterization
 test_audio_safety test_runtime_skills.SkillReplayTests'''.split(),
 'navigation': '''test_coordinate_reader test_corpse_coordinate test_navigation_coordinate
 test_navigation_steering test_mineral_hover test_mineral_monitor
 test_stationary_gather
 test_workflow_guards.NavigationGuardTests test_workflow_guards.SaleGuardTests'''.split(),
}

def flatten(suite):
 for item in suite:
  if isinstance(item,unittest.TestSuite):yield from flatten(item)
  else:yield item

def build_suite(names):
 loader=unittest.TestLoader()
 if 'all' in names:suite=loader.discover(str(ROOT),pattern='test_*.py')
 else:
  # Input revocation, death gate and serial limits are inexpensive shared guards.
  specs=list(dict.fromkeys(SUITES['core']+[s for name in names for s in SUITES[name]]))
  suite=loader.loadTestsFromNames(specs)
 # Do not hide invalid suite names or import errors as skipped work.
 if loader.errors:raise ValueError('\n'.join(loader.errors))
 tests={}
 for test in flatten(suite):tests.setdefault(test.id(),test)
 return unittest.TestSuite(tests.values())

def main():
 p=argparse.ArgumentParser(description='离线测试：默认战斗；可重复指定功能，all为完整回归。')
 p.add_argument('--suite',action='append',choices=[*SUITES,'all'])
 p.add_argument('--list',action='store_true',help='仅列出，不执行')
 p.add_argument('--report',type=Path)
 a=p.parse_args();os.chdir(ROOT);names=a.suite or ['combat']
 suite=build_suite(names);ids=[t.id() for t in flatten(suite)]
 scope="完整回归" if "all" in names else "其他功能未选中"
 print(f"范围：{', '.join(names)}；{len(ids)} 项唯一测试；{scope}。",flush=True)
 if a.list:
  print('\n'.join(ids));return 0
 result=unittest.TextTestRunner(verbosity=1).run(suite)
 if a.report:
  a.report.parent.mkdir(parents=True,exist_ok=True)
  a.report.write_text(json.dumps(dict(suites=names,tests_run=result.testsRun,
   failures=len(result.failures),errors=len(result.errors),
   skipped=[dict(test=t.id(),reason=r) for t,r in result.skipped],ids=ids),ensure_ascii=False,indent=2)+'\n')
 return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
