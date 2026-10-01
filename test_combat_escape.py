import unittest
from unittest.mock import Mock
from vision_state import Observation
from combat_escape import escape

class EscapeTests(unittest.TestCase):
    def test_stale_or_dead_never_sends_input(self):
        for obs,age in [(Observation(valid=True,in_combat=True),.6),(Observation(valid=False),.1)]:
            box=Mock();result=escape(lambda:(obs,age),box,Mock(),sleep=lambda _:None)
            self.assertEqual(result['state'],'ESCAPE_ABORTED');box.tap.assert_not_called()
    def test_clear_combat_requires_three_frames(self):
        box=Mock();observe=Mock(return_value=(Observation(valid=True,player_hp=.5),.1))
        self.assertEqual(escape(observe,box,Mock(),sleep=lambda _:None)['state'],'ESCAPED')
        self.assertEqual(observe.call_count,3);box.tap.assert_not_called()
    def test_escape_is_bounded_and_never_attacks(self):
        box=Mock();observe=Mock(return_value=(Observation(valid=True,in_combat=True,player_hp=.5),.1))
        self.assertEqual(escape(observe,box,Mock(),sleep=lambda _:None)['state'],'ESCAPE_LIMIT')
        self.assertEqual(box.tap.call_count,26)
        self.assertTrue(all(call.args[0] in (79,26) and call.args[1]<=500 for call in box.tap.call_args_list))

if __name__=='__main__':unittest.main()
