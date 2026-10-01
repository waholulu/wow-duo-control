"""Current runtime ownership and patrol-limit regressions."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from control_lock import controller_lock
from runtime_main import Controller, parser, validate
from runtime_types import Result


class ControllerOwnershipTests(unittest.TestCase):
    def test_single_controller_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            with controller_lock(folder):
                with self.assertRaisesRegex(RuntimeError, 'Another controller'):
                    with controller_lock(folder):
                        pass

    def test_unlimited_and_count_alias_are_current_parser_options(self):
        args=validate(parser().parse_args([
            '--execute','--output','unused','--count','12','--no-loot']))
        self.assertEqual(args.kills,12)
        self.assertEqual(args.rounds,0)
        self.assertTrue(args.no_loot)

    def test_round_limit_and_kill_limit_remain_independent(self):
        args=parser().parse_args([
            '--execute','--output','unused','--rounds','2','--kills','10','--no-loot'])
        controller=Controller(args,SimpleNamespace(),
                              SimpleNamespace(folder=Path('unused'),status=lambda **kw:None),{})
        def run(name,*unused,**kwargs):
            if name=='ready':return Result('completed','ready')
            if name=='combat':return Result('completed','xp_limit_out_of_combat',{'xp_events':1})
            return Result('completed','done')
        with patch.object(controller,'run_skill',side_effect=run):
            result=controller.run()
        self.assertEqual(result.reason,'ROUND_LIMIT_COMPLETE')
        self.assertEqual(result.facts['confirmed_kills'],2)

    def test_final_confirmed_kill_does_not_roam(self):
        args=parser().parse_args([
            '--execute','--output','unused','--kills','1','--no-loot'])
        controller=Controller(args,SimpleNamespace(),
                              SimpleNamespace(folder=Path('unused'),status=lambda **kw:None),{})
        calls=[]
        def run(name,*unused,**kwargs):
            calls.append(name)
            if name=='ready':return Result('completed','ready')
            if name=='combat':return Result('completed','xp_limit_out_of_combat',{'xp_events':1})
            return Result('completed','done')
        with patch.object(controller,'run_skill',side_effect=run):
            result=controller.run()
        self.assertEqual(result.reason,'KILL_LIMIT_COMPLETE')
        self.assertNotIn('roam',calls)


if __name__=='__main__':unittest.main()
