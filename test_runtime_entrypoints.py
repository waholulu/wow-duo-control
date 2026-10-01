import importlib
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from runtime_main import legacy_entry, parser, preflight, validate, Controller
from runtime_types import Result
from types import SimpleNamespace


class EntryTests(unittest.TestCase):
    def test_safety_diagnostic_is_read_only_without_execute(self):
        args=parser().parse_args(['--task','safety','--max-seconds','10','--output','unused'])
        validate(args)
        self.assertFalse(args.execute)

    def test_public_main_functions_always_delegate_to_unified_runtime(self):
        names=('patrol_hunt','hunt_loot','run_controller','bag_probe','loot_probe',
               'patrol_roam','combat_escape','navigate_local','vendor_probe','mineral_approach','optional_gather')
        for name in names:
            with self.subTest(module=name),patch('runtime_main.legacy_entry',return_value=17) as entry:
                module=importlib.import_module(name)
                self.assertEqual(module.main(['--help']),17)
                entry.assert_called_once_with(name+'.py',['--help'])

    def test_legacy_combat_seconds_and_log_have_explicit_translation(self):
        with patch('runtime_main.main',return_value=0) as entry:
            legacy_entry('run_controller.py',['--execute','--seconds','12','--log','runs/a.jsonl'])
        args=entry.call_args.args[0]
        self.assertEqual(args[:2],['--task','combat'])
        self.assertEqual(args[args.index('--output')+1],'runs/a-runtime')
        self.assertEqual(args[args.index('--combat-seconds')+1],'12')
        self.assertEqual(args[args.index('--stop-after-xp')+1],'0')

    def test_legacy_hunt_remains_finite_without_roaming(self):
        with patch('runtime_main.main',return_value=0) as entry:
            legacy_entry('hunt_loot.py',['--execute','--output','unused'])
        args=entry.call_args.args[0]
        self.assertEqual(args[:2],['--task','hunt'])
        self.assertEqual(args[args.index('--rounds')+1],'1')
        self.assertEqual(args[args.index('--reserve-slots')+1],'2')

    def test_legacy_dry_run_never_opens_input(self):
        with patch('runtime_main.main',return_value=0) as entry:
            legacy_entry('run_controller.py',['--seconds','2','--log','runs/a.jsonl'])
        args=entry.call_args.args[0]
        self.assertEqual(args[:2],['--task','observe'])
        self.assertNotIn('--execute',args)

    def test_unaccepted_core_fails_before_opening_any_hardware(self):
        from runtime_main import main
        with tempfile.TemporaryDirectory() as t,patch('vision_feed.Feed') as feed,patch('kmbox_tap.KMBox') as box,patch('builtins.print'):
            output=Path(t)/'run'
            self.assertEqual(main(['--execute','--output',str(output)]),2)
            result=json.loads((output/'result.json').read_text())
            self.assertEqual(result['reason'],'preflight_failed')
            feed.assert_not_called()
            box.assert_not_called()

    def test_missing_vision_profile_still_writes_final_state(self):
        from runtime_main import main
        with tempfile.TemporaryDirectory() as t,patch('builtins.print'):
            output=Path(t)/'run'
            self.assertEqual(main(['--task','observe','--profile',str(Path(t)/'missing.json'),'--output',str(output)]),2)
            self.assertEqual(json.loads((output/'result.json').read_text())['status'],'failed')
            self.assertTrue(json.loads((output/'recording.json').read_text())['worker_stopped'])

    def test_named_destination_resolves_and_requires_matching_verified_route(self):
        with tempfile.TemporaryDirectory() as t:
            places=Path(t)/'places.json'
            places.write_text(json.dumps(dict(places={'camp':dict(map='zone',coordinate=[2,3])},
                routes={'wrong':dict(map='zone',verified=True,points=[[1,1],[4,5]]),
                        'right':dict(map='zone',verified=True,points=[[1,1],[2,3]])})))
            args=parser().parse_args(['--task','navigate','--execute','--trial','--max-seconds','60',
                                     '--destination','camp','--map-id','zone','--places',str(places),'--output','unused'])
            profile=dict(core={'calibrated':True},navigation={'calibrated':True},map_id='zone')
            self.assertTrue(preflight(args,profile)['ready'])
            args.route='wrong'
            wrong=preflight(args,profile)
            self.assertFalse(wrong['ready'])
            self.assertIn('Verified route does not end at the requested destination',wrong['issues'])
            args.route='right'
            self.assertTrue(preflight(args,profile)['ready'])
            args.route=None
            ctl=Controller(args,SimpleNamespace(),SimpleNamespace(folder=Path(t)),profile)
            with patch.object(ctl,'navigation',return_value=Result('completed','ARRIVED')) as navigate:
                ctl.run()
            navigate.assert_called_once_with([(2,3)],True)

    def test_named_destination_map_mismatch_is_blocked_before_hardware(self):
        with tempfile.TemporaryDirectory() as t:
            places=Path(t)/'places.json'
            places.write_text(json.dumps(dict(places={'camp':dict(map='other',coordinate=[2,3])},routes={})))
            args=parser().parse_args(['--task','navigate','--execute','--trial','--max-seconds','60',
                                     '--destination','camp','--map-id','zone','--places',str(places),'--output','unused'])
            profile=dict(core={'calibrated':True},navigation={'calibrated':True},map_id='zone')
            self.assertFalse(preflight(args,profile)['ready'])

    def test_recording_close_failure_still_writes_failed_terminal_state(self):
        from runtime_main import main
        from runtime_store import Store
        original_close=Store.close
        def broken_close(store):
            original_close(store)
            raise OSError('simulated close metadata failure')
        with tempfile.TemporaryDirectory() as t,patch.object(Store,'close',broken_close),patch('builtins.print'):
            output=Path(t)/'run'
            self.assertEqual(main(['--task','preflight','--output',str(output)]),2)
            self.assertEqual(json.loads((output/'result.json').read_text())['reason'],'recording_close_failed')
            self.assertEqual(json.loads((output/'status.json').read_text())['status'],'failed')


if __name__=='__main__':unittest.main()
