import json,tempfile,unittest
from pathlib import Path
from death_review import require_review,blocking_review,gate_path
class DeathReviewTests(unittest.TestCase):
 def test_persistent_gate_does_not_block_safe_recovery(self):
  with tempfile.TemporaryDirectory() as root:
   require_review(root,'paladin','run1','zero_hp')
   self.assertTrue(blocking_review(root,'paladin','patrol'))
   self.assertIsNone(blocking_review(root,'paladin','recover'))
   require_review(root,'paladin','run2','zero_hp')
   self.assertTrue(json.loads(gate_path(root,'paladin').read_text())['run'].endswith('run1'))
 def test_resolved_requires_documented_review(self):
  with tempfile.TemporaryDirectory() as root:
   require_review(root,'paladin','run','zero_hp');p=gate_path(root,'paladin');d=json.loads(p.read_text());d['status']='resolved';p.write_text(json.dumps(d))
   self.assertTrue(blocking_review(root,'paladin','combat'))
   report=Path(root)/'review.json';d['review_file']=str(report);p.write_text(json.dumps(d));report.write_text(json.dumps(dict(evidence=['run'],findings=['loss'],fixes=['stop'],verification=['test'],resume_conclusion='bounded test',resume_allowed=True)))
   self.assertIsNone(blocking_review(root,'paladin','combat'))
if __name__=='__main__':unittest.main()
