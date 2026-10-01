import json
from pathlib import Path
import tempfile
import unittest
from runtime_review import build_review, verify_sources


class ReviewIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.run=self.root/'run';self.run.mkdir()
        for name,value in (('result.json',{'status':'failed','reason':'receipt_unconfirmed'}),
                           ('config.json',{'task':'fish'}),('run.json',{'version':'test'}),
                           ('recording.json',{'dropped_records':0,'writer_stopped':True})):
            (self.run/name).write_text(json.dumps(value))
        (self.run/'events.jsonl').write_text('{"kind":"action_sent","reason":"reel_fishing","at":1}\n')

    def review(self):return build_review(self.run,self.root/'review')

    def test_timeline_and_nested_evidence_are_hashed_and_detect_changes(self):
        folder=self.run/'evidence'/'sample';folder.mkdir(parents=True)
        (folder/'manifest.json').write_text(json.dumps(dict(pre_window_complete=True,post_window_complete=True,frame_times=[[1,1]],audio_available=True)))
        frame=folder/'00000001.jpg';frame.write_bytes(b'fictional image fixture')
        (folder/'audio.jsonl').write_text('{"fictional_audio_fixture":true}\n')
        facts=self.review()
        self.assertTrue(facts['complete'])
        self.assertTrue(any(r['relative_path']=='events.jsonl' for r in facts['files']))
        self.assertTrue(any(r['relative_path']=='evidence/sample/audio.jsonl' for r in facts['files']))
        self.assertEqual(verify_sources(facts),[])
        frame.write_bytes(b'changed')
        self.assertEqual(verify_sources(facts),[dict(path=str(frame.resolve()),reason='changed')])
        timeline=json.loads((self.root/'review'/'timeline.jsonl').read_text())
        self.assertEqual(timeline['source_line'],1)
        self.assertFalse(json.loads((self.root/'review'/'proposal.json').read_text())['activate'])

    def test_truncated_event_keeps_valid_timeline_and_marks_gap(self):
        with (self.run/'events.jsonl').open('a') as stream:stream.write('{"kind":"unfinished"')
        facts=self.review()
        self.assertFalse(facts['complete'])
        self.assertEqual(facts['event_counts']['action_sent'],1)
        self.assertTrue(any(g['reason']=='unreadable_event' for g in facts['evidence_gaps']))

    def test_evicted_recordings_and_missing_referenced_frames_are_explicit(self):
        (self.run/'recording.json').write_text(json.dumps(dict(evicted_clips=2,event_rotations=3)))
        folder=self.run/'evidence'/'missing';folder.mkdir(parents=True)
        (folder/'manifest.json').write_text(json.dumps(dict(pre_window_complete=False,post_window_complete=False,frame_times=[[7,1]],audio_available=True)))
        facts=self.review();reasons={r['reason'] for r in facts['evidence_gaps']}
        self.assertTrue({'evicted_clips','event_rotations','incomplete_event_window','referenced_frame_missing','referenced_audio_missing'}<=reasons)
        self.assertFalse(facts['complete'])

    def test_historical_unconfirmed_terminal_is_a_failure_fact(self):
        (self.run/'result.json').write_text('{"verified":false,"reason":"no_new_loot_messages"}')
        facts=self.review()
        self.assertTrue(any(r['kind']=='terminal_result' for r in facts['failures']))

    def test_review_cannot_recursively_export_itself(self):
        with self.assertRaisesRegex(ValueError,'outside'):build_review(self.run,self.run/'review')
        self.assertFalse((self.run/'review').exists())


if __name__=='__main__':unittest.main()
