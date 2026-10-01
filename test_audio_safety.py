import base64
from collections import deque
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave
import numpy as np

from runtime_audio import AudioSource, SoundDetector, features, fishing, load_sound, validate_audio_config
from runtime_types import Event
from test_support import Driver, immediate


class MemoryStore:
    def __init__(self):self.events=[];self.chunks=[]
    def emit(self,kind,**values):self.events.append(dict(kind=kind,**values))
    def audio_chunk(self,row):self.chunks.append(row)


def source(config=None):
    value=AudioSource.__new__(AudioSource)
    value.config=dict(device_uid='explicit-usb',offset_seconds=0,templates={},**(config or {}))
    value.store=MemoryStore()
    value.detector=SoundDetector({})
    value.error=None;value.last_at=0;value.last_sample_end=None;value.sequence=0;value.ready=False
    value.maximum_rms=0;value.events=deque(maxlen=100);value.lock=threading.Lock()
    value.generation='session-a';value.stop_event=threading.Event()
    return value


def ready(**kw):
    return dict(dict(kind='ready',device_uid='explicit-usb',clock='mach_absolute_time',host_time=10),**kw)


def samples(**kw):
    pcm=np.round(np.sin(np.arange(8000)*2*np.pi*800/16000)*3276).astype('<i2')
    return dict(dict(kind='samples',sequence=1,bits=16,float=False,little_endian=True,rate=16000,
                     channels=1,sample_time=10,host_time=10.51,pcm=base64.b64encode(pcm.tobytes()).decode()),**kw)


class AudioSafetyTests(unittest.TestCase):
    def test_invalid_calibration_and_template_fail_before_opening_device(self):
        for offset in (float('nan'),float('inf'),True,'0'):
            with self.subTest(offset=offset),patch('runtime_audio.subprocess.Popen') as process:
                with self.assertRaises(ValueError):AudioSource(dict(device_uid='usb',offset_seconds=offset),MemoryStore())
                process.assert_not_called()
        with patch('runtime_audio.subprocess.Popen') as process:
            with self.assertRaises(FileNotFoundError):
                AudioSource(dict(device_uid='usb',templates={'bite':{'file':'does-not-exist.wav'}}),MemoryStore())
            process.assert_not_called()

    def test_silent_templates_and_unbounded_thresholds_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'silence.wav'
            with wave.open(str(path),'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
                audio.writeframes(bytes(16000))
            with self.assertRaisesRegex(ValueError,'non-silent'):load_sound(path)
        for field,value in (('threshold',float('nan')),('minimum_rms',-1),('refractory_seconds',0),('event_offset_seconds',.51)):
            with self.assertRaises(ValueError):validate_audio_config(dict(device_uid='usb',templates={'bite':dict(file='sample.wav',**{field:value})}))

    def test_capture_clock_preserves_acquisition_and_offset_in_recording(self):
        audio=source();audio.config['offset_seconds']=-.05
        audio._consume(ready(),10.2)
        audio._consume(samples(),10.6)
        self.assertEqual(audio.store.chunks[0]['at'],9.95)
        self.assertEqual(audio.store.chunks[0]['captured_at'],10)
        self.assertTrue(audio.store.chunks[0]['synchronized'])
        # The 200 ms ready-message delivery delay never shifts sample time.
        self.assertEqual(audio.last_at,10.5)

    def test_unknown_device_or_restart_cannot_keep_old_session(self):
        audio=source()
        with self.assertRaisesRegex(ValueError,'wrong device'):audio._consume(ready(device_uid='microphone'),10)
        audio._consume(ready(),10)
        with self.assertRaisesRegex(ValueError,'restarted'):audio._consume(ready(),10)

    def test_old_future_or_discontinuous_pcm_is_rejected(self):
        for change,now in (({'sample_time':9},10.6),({'sample_time':11},10.6),
                           ({'sequence':2},10.6),({'sequence':True},10.6),
                           ({'rate':48000},10.6),({'pcm':'not base64!'},10.6),
                           ({'sample_time':float('nan')},10.6)):
            with self.subTest(change=change):
                audio=source();audio._consume(ready(),10)
                with self.assertRaises((ValueError,TypeError)):audio._consume(samples(**change),now)
        audio=source();audio._consume(ready(),10);audio._consume(samples(),10.6)
        with self.assertRaisesRegex(ValueError,'timeline discontinuity'):
            audio._consume(samples(sequence=2,sample_time=10.55),11.1)

    def test_stream_failure_clears_events_and_exposes_error(self):
        audio=source()
        audio.events.append(Event('bite',10,'audio','old',{'generation':'session-a'}))
        audio.proc=SimpleNamespace(stdout=[json.dumps(ready()),json.dumps({'error':'USB disconnected'})])
        with patch('runtime_audio.time.monotonic',return_value=10):audio._run()
        self.assertEqual(audio.error,'USB disconnected')
        self.assertEqual(list(audio.events),[])
        self.assertEqual(audio.since(0,'bite'),[])
        self.assertEqual(audio.store.events[-1]['kind'],'audio_unavailable')

    def test_detector_does_not_join_samples_across_generation(self):
        pcm=np.sin(np.arange(8000)*2*np.pi*800/16000)*.1
        with patch('runtime_audio.load_sound',return_value=features(pcm,16000)):
            detector=SoundDetector({'bite':{'file':'unused','threshold':.9}})
        self.assertEqual(detector.update(pcm[:4000],16000,1,'old'),[])
        self.assertEqual(detector.update(pcm[4000:],16000,1.25,'new'),[])
        events=detector.update(pcm[4000:],16000,1.5,'new')
        self.assertEqual(len(events),1)
        self.assertEqual(events[0].at,1.0)
        self.assertEqual(events[0].evidence['window_start'],1.0)
        self.assertEqual(events[0].evidence['generation'],'new')

    def test_unsynchronized_source_records_but_cannot_authorize_events(self):
        audio=source();audio.config['offset_seconds']=None
        audio._consume(ready(),10)
        audio._consume(samples(),10.6)
        self.assertFalse(audio.store.chunks[0]['synchronized'])
        self.assertEqual(list(audio.events),[])

    def test_visual_fallback_requires_boolean_true(self):
        driver=Driver()
        result=driver.run(fishing(driver.ctx,{'visual_verified':'false'},None,'unused',1))
        self.assertEqual(result.reason,'audio_unavailable')
        self.assertEqual(driver.actions,[])

    def test_reel_deadline_and_connection_are_checked_at_admission(self):
        driver=Driver();position=dict(x=1000,y=600,kind='hand')
        visual=SimpleNamespace(cursor=lambda *args:position)
        audio=SimpleNamespace(healthy=lambda:True,error=None,generation='one',
                              since=lambda *args:[SimpleNamespace(at=driver.now,identity='bite-1',evidence={})])
        spec=dict(cast_key=31,receipt_items=['鱼'],bobber_template='unused',world_roi=[1,2,3,4],wait_seconds=5)
        with patch('runtime_audio.LootVision',return_value=visual),patch('runtime_audio.template',return_value=position),\
             patch('runtime_audio.read_receipts',side_effect=lambda *args:immediate({})),\
             patch('runtime_audio.receipt_confirmation',side_effect=lambda *args:immediate(True)):
            driver.run(fishing(driver.ctx,spec,audio,'unused',1))
        reel=next(a for a in driver.actions if a.reason=='reel_fishing')
        self.assertLessEqual(reel.expires_at,reel.snapshot.captured_at+.5)
        audio.generation='two'
        self.assertFalse(reel.admission_guard())
        audio.generation='one';audio.healthy=lambda:False
        self.assertFalse(reel.admission_guard())
        audio.healthy=lambda:True;driver.now+=1
        self.assertFalse(reel.admission_guard())

    def test_pre_cast_audio_window_cannot_reel_current_cast(self):
        driver=Driver();position=dict(x=1000,y=600,kind='hand')
        audio=SimpleNamespace(healthy=lambda:True,error=None,
            since=lambda *args:[Event('bite',driver.now,'audio','cross-cast',{'window_start':-1})])
        spec=dict(cast_key=31,receipt_items=['鱼'],bobber_template='unused',world_roi=[1,2,3,4],wait_seconds=.1)
        with patch('runtime_audio.LootVision',return_value=SimpleNamespace(cursor=lambda *args:position)),\
             patch('runtime_audio.template',return_value=position),\
             patch('runtime_audio.read_receipts',side_effect=lambda *args:immediate({})):
            result=driver.run(fishing(driver.ctx,spec,audio,'unused',1))
        self.assertEqual(result.facts['received'],0)
        self.assertEqual([a.reason for a in driver.actions],['cast_fishing'])


if __name__=='__main__':unittest.main()
