"""Lifecycle failure integration; every device and controller is mocked."""
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_main import main
from runtime_types import Result


class LifecycleTests(unittest.TestCase):
    def test_worker_close_failure_replaces_success_and_persists_terminal_result(self):
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            stack.enter_context(patch('control_lock.controller_lock',return_value=nullcontext()))
            stack.enter_context(patch('vision_feed.Feed'))
            stack.enter_context(patch('vision_state.Vision'))
            source=stack.enter_context(patch('runtime_main.Perception')).return_value
            executor=stack.enter_context(patch('runtime_main.Executor')).return_value
            scheduler=stack.enter_context(patch('runtime_main.Scheduler')).return_value
            scheduler.close.side_effect=RuntimeError('Recognition workers did not stop: recognition-0')
            stack.enter_context(patch('runtime_main.Controller.run',return_value=Result('completed','test_complete')))
            stack.enter_context(patch('builtins.print'))
            box=stack.enter_context(patch('kmbox_tap.KMBox'))
            output=Path(folder)/'run'
            status=main(['--task','observe','--max-seconds','1','--output',str(output)])
            result=json.loads((output/'result.json').read_text())
            self.assertEqual(status,2)
            self.assertEqual(result['status'],'failed')
            self.assertIn('Recognition workers did not stop',result['reason'])
            self.assertEqual(json.loads((output/'status.json').read_text())['status'],'failed')
            self.assertTrue(json.loads((output/'recording.json').read_text())['worker_stopped'])
            executor.close.assert_called_once()
            source.close.assert_called_once()
            box.assert_not_called()


if __name__=='__main__':
    unittest.main()
