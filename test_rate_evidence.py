import json
import tempfile
import unittest
from pathlib import Path
from rate_evidence import analyze


class RateEvidenceTests(unittest.TestCase):
    def test_five_fixed_minutes_require_controller_paired_xp_in_each(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            rows=[]
            for i in range(10):
                start=60*(i//2)+10+20*(i%2)
                task=f'{i:04d}-combat'
                rows.extend([{'kind':'skill_started','task':task,'at':start},
                             {'kind':'observation','at':start+1,'frame':i+1,
                              'observation':{'xp_visible':True}},
                             {'kind':'skill_finished','task':task,'at':start+5,
                              'elapsed_seconds':5,
                              'result':{'reason':'xp_limit_out_of_combat',
                                        'facts':{'xp_events':1}}}])
            rows.append({'kind':'observation','at':301,'frame':11,'observation':{'xp_visible':False}})
            (folder/'events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            safe_result={'status':'cancelled','reason':'run_deadline',
                         'facts':{'confirmed_kills':10,'safety_exit':'peace_confirmed'}}
            (folder/'result.json').write_text(json.dumps(safe_result))
            report=analyze(folder)
            self.assertEqual(report['kills_per_minute'],[2]*5)
            self.assertEqual(report['stage_breakdown']['combat:xp_limit_out_of_combat'],
                             {'count':10,'seconds':50})
            self.assertTrue(report['rate_met'])
            for status,reason,safety in [('failed','skill_deadline','peace_confirmed'),
                                         ('cancelled','stop_requested','peace_confirmed'),
                                         ('cancelled','run_deadline','threat_unresolved')]:
                invalid=dict(safe_result,status=status,reason=reason,
                             facts=dict(safe_result['facts'],safety_exit=safety))
                (folder/'result.json').write_text(json.dumps(invalid))
                self.assertFalse(analyze(folder)['rate_met'])
            (folder/'result.json').write_text(json.dumps(safe_result))
            rows[7]['observation']['xp_visible']=False
            (folder/'events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            self.assertFalse(analyze(folder)['rate_met'])


if __name__=='__main__':unittest.main()
