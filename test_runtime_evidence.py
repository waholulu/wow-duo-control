import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from runtime_store import Store
from runtime_types import Result, Snapshot
from runtime_review import build_review
from runtime_acceptance import evaluate
from vision_state import Observation


class EvidenceTests(unittest.TestCase):
    def test_required_fixtures_are_present_and_immutable(self):
        root=Path(__file__).parent/'tests/fixtures'
        for item in json.loads((root/'manifest.json').read_text()):
            path=root/item['path']
            with self.subTest(path=item['path']):
                self.assertTrue(path.is_file(),'Required evidence cannot be skipped')
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item['sha256'])

    def test_cancelled_clip_marks_incomplete_window_and_review_has_no_activation(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            store=Store(root/'run',{})
            store.frame(Snapshot(1,0,(1,0,0),Observation(),np.zeros((16,16,3),np.uint8)))
            store.clip('cancelled')
            store.result(Result('cancelled','stop_requested'))
            store.close()
            manifests=list((root/'run/evidence').glob('*/manifest.json'))
            self.assertEqual(len(manifests),1)
            self.assertFalse(json.loads(manifests[0].read_text())['post_window_complete'])
            result=build_review(root/'run',root/'review')
            self.assertEqual(result['result']['status'],'cancelled')
            self.assertFalse(json.loads((root/'review/proposal.json').read_text())['activate'])

    def test_missing_acceptance_data_never_passes(self):
        with tempfile.TemporaryDirectory() as t:
            result=evaluate({},t)
            self.assertFalse(result['all_passed'])
            self.assertFalse(any(stage['passed'] for stage in result['stages'].values()))

    def test_duplicate_trials_or_missing_evidence_do_not_count(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t,'evidence').write_text('{}')
            one=dict(id='same',kind='hunt_loot_round',passed=True,human_intervention=False,evidence=['evidence'])
            result=evaluate({'trials':[one,one,dict(one,id='missing',evidence=['missing'])]},t)
            self.assertEqual(result['stages']['core']['counts']['hunt_loot_round'],1)
            self.assertEqual(len(result['invalid_evidence']),2)


if __name__=='__main__':unittest.main()
