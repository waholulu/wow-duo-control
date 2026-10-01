import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime_store import Store, window_covered
from runtime_world import Capabilities, MineralTracker, Places, profile_digest
from runtime_replay import ReplaySource, replay
from vision_state import Observation


class RecordingIntegrityTests(unittest.TestCase):
    def test_elapsed_wall_time_does_not_claim_missing_post_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'run',{})
            clip=dict(id='gap',at=10,until=20,reason='test',
                frames=[(1,5,b'jpeg'),(2,10,b'jpeg'),(3,20,b'jpeg')],audio=[])
            store._finish_clip(clip)
            store.close()
            manifest=json.loads((store.folder/'evidence/gap/manifest.json').read_text())
            self.assertTrue(manifest['video_available'])
            self.assertFalse(manifest['video_pre_complete'])
            self.assertFalse(manifest['video_post_complete'])
            self.assertFalse(manifest['audio_available'])
            self.assertFalse(manifest['post_window_complete'])

    def test_complete_clip_requires_continuous_audio_and_video(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'run',{})
            frames=[(n,5+n*.125,b'jpeg') for n in range(121)]
            audio=[dict(at=5+n*.1,rate=16000,channels=1,bits=16,synchronized=True,
                        pcm=base64.b64encode(b'\0'*3200).decode()) for n in range(150)]
            store._finish_clip(dict(id='full',at=10,until=20,reason='test',frames=frames,audio=audio))
            store.close()
            manifest=json.loads((store.folder/'evidence/full/manifest.json').read_text())
            self.assertTrue(manifest['pre_window_complete'])
            self.assertTrue(manifest['post_window_complete'])
            self.assertFalse(window_covered([(0,1),(2,3)],0,3,.1))

    def test_clip_retention_loss_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'run',{},max_clips=1)
            for number in range(2):
                store._finish_clip(dict(id=str(number),at=10,until=20,reason='test',frames=[],audio=[]))
            store.close()
            report=json.loads((store.folder/'recording.json').read_text())
            self.assertEqual(report['evicted_clips'],1)
            self.assertEqual(len(list((store.folder/'evidence').iterdir())),1)

    def test_disk_budget_includes_skill_artifacts_without_deleting_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'run',{},max_bytes=10000)
            artifact=store.folder/'navigation-ocr.png'
            artifact.write_bytes(b'evidence'*2000)
            self.assertFalse(store._check_budget())
            store.close()
            self.assertEqual(store.error,'recording_disk_budget_exceeded')
            self.assertTrue(artifact.is_file())

    def test_route_and_custom_calibration_changes_invalidate_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'places.json'
            path.write_text('{"route":[[1,2],[3,4]]}')
            profile={'navigation':{'calibrated':True}}
            proof={'navigation':dict(offline_passed=True,live_passed=True,
                                    profile_sha256=profile_digest(profile,(path,)))}
            cap=Capabilities(profile,proof,extra_paths=(path,))
            self.assertTrue(cap.require('navigation')['calibrated'])
            path.write_text('{"route":[[1,2],[9,9]]}')
            with self.assertRaisesRegex(ValueError,'acceptance_required'):
                cap.require('navigation')

    def test_places_reject_unknown_maps_and_malformed_coordinates(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'places.json'
            path.write_text(json.dumps({'places':{'vendor':{'map':'area','coordinate':[True,2]}},
                'routes':{'bad':{'map':'unconfirmed','verified':True,'points':[[1,2],[3,4]]}}}))
            places=Places(path)
            with self.assertRaises(ValueError):places.place('vendor','area')
            with self.assertRaises(ValueError):places.route('bad','unconfirmed')
            with self.assertRaises(ValueError):places.remember('vendor','area',[1,float('nan')])

    def test_duplicate_frame_cannot_stabilize_mineral(self):
        tracker=MineralTracker()
        for _ in range(5):self.assertEqual(tracker.update([dict(x=1,y=2)],1),[])
        self.assertEqual(tracker.tracks[0]['frames'],1)

    def test_replay_preserves_unknown_states_and_calibration_identity(self):
        class Recorder:
            def emit(self,*args,**kwargs):pass
        row=dict(at=0,calibration_generation=7,target_track='seen-3',observation={'valid':True})
        source=ReplaySource({'frames':[row]},'.',None,Recorder())
        source.worker.join(timeout=1)
        try:
            snapshot=source.peek()
            self.assertIsNotNone(snapshot)
            self.assertFalse(snapshot.combat_known)
            self.assertFalse(snapshot.casting_known)
            self.assertEqual(snapshot.calibration_generation,7)
            self.assertEqual(snapshot.target_track,'seen-3')
        finally:source.close()

    def test_replay_does_not_refresh_delayed_observation_timestamp(self):
        class Recorder:
            def emit(self,*args,**kwargs):pass
            def frame(self,*args):pass
        class SlowVision:
            def observe(self,frame):
                time.sleep(.08)
                return Observation(valid=True)
        with patch('runtime_replay.cv2.imread',return_value=object()):
            source=ReplaySource({'frames':[dict(at=0,image='fixture.jpg')]},'.',SlowVision(),Recorder())
            source.worker.join(timeout=1)
            try:
                self.assertGreaterEqual(time.monotonic()-source.peek().captured_at,.075)
            finally:source.close()

    def test_replay_invalid_timestamps_and_empty_assertions_do_not_pass(self):
        for value in (True,float('nan'),float('inf'),-1):
            with self.assertRaises(ValueError):ReplaySource({'frames':[dict(at=value)]},'.',None,None)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            manifest=root/'input.json'
            manifest.write_text(json.dumps({'frames':[dict(at=0,age_seconds=1,
                observation={'valid':True,'player_hp':1})]}))
            report=replay(manifest,root/'result')
            self.assertFalse(report['expectations_passed'])
            self.assertFalse(report['hardware_opened'])


if __name__=='__main__':unittest.main()

class AtomicConcurrentStatusTest(unittest.TestCase):
    def test_concurrent_writers_have_distinct_temporary_files(self):
        import tempfile,json
        from pathlib import Path
        from concurrent.futures import ThreadPoolExecutor
        from runtime_store import atomic_json
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'status.json'
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i:atomic_json(p,{'writer':i}),range(40)))
            self.assertIn(json.loads(p.read_text())['writer'],range(40))
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])
