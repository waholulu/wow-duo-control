"""Explicit USB capture, local spectral event matching, contextual deduplication."""
from collections import deque
import base64
import json
import math
from pathlib import Path
import subprocess
import threading
import time
import wave
import numpy as np
from runtime_types import Event, Result
from runtime_skills import fresh, tap, move_pointer
from runtime_interactions import template, read_receipts, receipt_confirmation
from interaction_vision import LootVision

ROOT=Path(__file__).resolve().parent


def finite_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def validate_audio_config(config, require_detection=False):
    """Validate before opening the helper; missing calibration is record-only."""
    if not isinstance(config.get('device_uid'),str) or not config['device_uid'].strip():
        raise ValueError('Audio device UID must be explicitly selected')
    offset=config.get('offset_seconds')
    if offset is not None and not finite_number(offset):
        raise ValueError('Audio/video offset must be finite')
    if require_detection and offset is None:
        raise ValueError('Audio/video offset calibration is required')
    specs=config.get('templates',{})
    if not isinstance(specs,dict):
        raise ValueError('Audio templates must be a mapping')
    for spec in specs.values():
        if not isinstance(spec,dict) or not isinstance(spec.get('file'),str):
            raise ValueError('Audio template file is required')
        for key,default,low,high in (('threshold',.92,0,1),('minimum_rms',.005,0,1),
                                    ('refractory_seconds',2,.5,60),('event_offset_seconds',0,0,.5)):
            value=spec.get(key,default)
            if not finite_number(value) or not low<=value<=high:
                raise ValueError('Invalid audio template '+key)


def features(samples, rate):
    samples=np.asarray(samples,dtype=float)
    if len(samples)<int(rate*.1):
        return None
    # Time-frequency pattern, not loudness alone. Fixed 16 kHz analysis grid.
    if rate!=16000:
        samples=np.interp(np.arange(0,len(samples),rate/16000),np.arange(len(samples)),samples)
    samples=samples[-8000:]
    if len(samples)<8000:
        samples=np.pad(samples,(8000-len(samples),0))
    frames=np.stack([samples[n:n+512]*np.hanning(512) for n in range(0,7489,256)])
    spectrum=np.abs(np.fft.rfft(frames,axis=1))[:,2:194]
    bands=np.log1p(spectrum.reshape(len(frames),24,8).mean(axis=2))
    flat=bands.ravel()
    flat-=flat.mean()
    norm=np.linalg.norm(flat)
    return flat/norm if norm>1e-8 else np.zeros_like(flat)


def load_sound(path):
    with wave.open(str(path),'rb') as audio:
        if audio.getsampwidth()!=2:
            raise ValueError('Sound templates must be signed 16-bit PCM WAV')
        pcm=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype(float)/32768
        pcm=pcm.reshape(-1,audio.getnchannels()).mean(axis=1)
        feature=features(pcm,audio.getframerate())
        if feature is None or not np.all(np.isfinite(feature)) or np.linalg.norm(feature)<.5:
            raise ValueError('Sound templates must contain a non-silent event')
        return feature


class SoundDetector:
    def __init__(self, specs):
        self.specs=specs
        self.templates={name:load_sound(ROOT/spec['file']) for name,spec in specs.items()}
        self.samples=np.empty(0,dtype=float)
        self.last={}
        self.number=0
        self.generation=None

    def update(self, pcm, rate, at, generation):
        if generation!=self.generation:
            self.samples=np.empty(0,dtype=float)
            self.last.clear()
            self.generation=generation
        if rate!=16000:
            pcm=np.interp(np.arange(0,len(pcm),rate/16000),np.arange(len(pcm)),pcm)
        self.samples=np.concatenate([self.samples,pcm])[-8000:]
        if len(self.samples)<8000:
            return []
        feature=features(self.samples,16000)
        rms=float(np.sqrt(np.mean(self.samples**2)))
        events=[]
        for name,reference in self.templates.items():
            spec=self.specs[name]
            score=float(np.dot(feature,reference))
            if score>=spec.get('threshold',.92) and rms>=spec.get('minimum_rms',.005) and at-self.last.get(name,-1e10)>=spec.get('refractory_seconds',2):
                self.number+=1
                self.last[name]=at
                # `at` is the end of acquired samples, not the callback time.
                # The default anchor is conservative; a labeled template may
                # supply the onset's offset within the half-second window.
                event_at=at-.5+spec.get('event_offset_seconds',0)
                events.append(Event(name,event_at,'audio',f'{generation}:{self.number}',
                                    dict(score=score,rms=rms,window_start=at-.5,window_end=at,
                                         generation=generation)))
        return events


class AudioSource:
    def __init__(self, config, store):
        validate_audio_config(config)
        uid=config['device_uid']
        # Bad/missing templates fail synchronously, before a device is opened.
        self.detector=SoundDetector(config.get('templates',{}))
        self.store=store
        self.config=config
        self.error=None
        self.last_at=0
        self.last_sample_end=None
        self.sequence=0
        self.ready=False
        self.maximum_rms=0.0
        self.events=deque(maxlen=100)
        self.lock=threading.Lock()
        self.generation=str(time.time_ns())
        self.stop_event=threading.Event()
        self.proc=subprocess.Popen([str(ROOT/'audio_capture'),'--device',uid],stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,text=True,bufsize=1)
        self.worker=threading.Thread(target=self._run,name='audio-events',daemon=True)
        self.worker.start()

    def _run(self):
        try:
            for line in self.proc.stdout:
                if self.stop_event.is_set():
                    break
                row=json.loads(line)
                self._consume(row,time.monotonic())
            if not self.stop_event.is_set():
                raise RuntimeError('Audio stream ended')
        except Exception as exc:
            with self.lock:
                self.error=str(exc)
                self.events.clear()
            self.store.emit('audio_unavailable',reason=self.error)

    def _consume(self,row,now):
        if row.get('error'):
            raise RuntimeError(row['error'])
        if row.get('kind')=='ready':
            if self.ready:
                raise ValueError('Audio session restarted; time synchronization must be confirmed again')
            if row.get('device_uid')!=self.config['device_uid']:
                raise ValueError('Audio helper selected the wrong device')
            # Both clocks use mach_absolute_time. Deriving an offset from a
            # received line would hide helper startup/pipe/capture latency.
            if row.get('clock')!='mach_absolute_time' or 'mach_absolute_time' not in time.get_clock_info('monotonic').implementation:
                raise ValueError('Unsupported audio monotonic clock')
            host=row.get('host_time')
            if not finite_number(host) or not 0<=now-host<=.5:
                raise ValueError('Audio clock validation failed or ready message is stale')
            self.ready=True
            self.store.emit('audio_clock_confirmed',device_uid=self.config['device_uid'],
                            generation=self.generation,ready_delivery_seconds=now-host,
                            offset_seconds=self.config.get('offset_seconds'))
            return
        if row.get('kind')!='samples' or not self.ready:
            raise ValueError('Audio samples arrived without a verified session')
        if (row.get('bits')!=16 or row.get('float') is not False or row.get('channels')!=1
                or row.get('rate')!=16000 or row.get('little_endian') is not True):
            raise ValueError('Unexpected PCM format')
        sequence=row.get('sequence')
        if type(sequence) is not int or sequence!=self.sequence+1:
            raise ValueError('Audio sequence discontinuity')
        at=row.get('sample_time')
        if not finite_number(at):
            raise ValueError('Invalid audio sample timestamp')
        raw=base64.b64decode(row['pcm'],validate=True)
        if not raw or len(raw)%2 or len(raw)>32000:
            raise ValueError('Invalid audio PCM payload')
        pcm=np.frombuffer(raw,dtype='<i2').astype(float)/32768
        end=at+len(pcm)/16000
        if not 0<=now-end<=.5:
            raise ValueError('Audio samples are stale or in the future')
        if self.last_sample_end is not None and abs(at-self.last_sample_end)>.02:
            raise ValueError('Audio timeline discontinuity')
        self.sequence=sequence
        self.last_sample_end=end
        self.last_at=end
        self.maximum_rms=max(self.maximum_rms,float(np.sqrt(np.mean(pcm**2))))
        correction=self.config.get('offset_seconds')
        self.store.audio_chunk(dict(row,at=at+(correction or 0),captured_at=at,
                                    generation=self.generation,synchronized=correction is not None))
        if correction is None:
            return
        corrected_end=end+correction
        if not 0<=now-corrected_end<=.5:
            raise ValueError('Calibrated audio/video timestamp is stale or in the future')
        for event in self.detector.update(pcm,16000,corrected_end,self.generation):
            with self.lock:
                self.events.append(event)
            self.store.emit('perception_event',event=event.__dict__)

    def since(self, at, kind):
        with self.lock:
            if not self.healthy():
                return []
            return [e for e in self.events if e.at>=at and e.kind==kind
                    and e.evidence.get('generation')==self.generation]

    def healthy(self):
        return not self.stop_event.is_set() and self.ready and not self.error and 0<=time.monotonic()-self.last_at<=.5

    def close(self):
        self.stop_event.set()
        with self.lock:
            self.events.clear()
        if self.proc.poll() is None:self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.worker.join(timeout=2)
        self.proc.stdout.close()
        self.proc.stderr.close()
        self.store.emit('audio_summary',device_uid=self.config['device_uid'],received=self.last_at>0,
                        maximum_rms=self.maximum_rms,non_silent=self.maximum_rms>.001,error=self.error)


def fishing(ctx, spec, audio, folder, casts):
    cursor=LootVision()
    visual_verified=spec.get('visual_verified') is True
    audio_deadline=min(ctx.deadline,ctx.clock()+5)
    while audio and not audio.healthy() and not audio.error and ctx.clock()<audio_deadline:
        yield from ctx.pause(.05)
    received=ctx.checkpoint.get('received',0)
    start_cast=ctx.checkpoint.get('cast',0)
    for number in range(start_cast,casts):
        ctx.checkpoint['cast']=number+1
        ctx.phase='fishing_cast'
        if (audio is None or not audio.healthy()) and not visual_verified:
            return Result('failed','audio_unavailable',dict(casts=number,received=received))
        before=yield from read_receipts(ctx,folder,spec['receipt_items'],f'fish-{number}-before')
        yield from tap(ctx,spec['cast_key'],80,'cast_fishing',.8,True)
        cast_at=ctx.clock()
        ctx.phase='fishing_wait'
        until=min(ctx.deadline,cast_at+spec.get('wait_seconds',30))
        consumed=False
        reel_sent=False
        positioned=None
        while ctx.clock()<until:
            s=yield from fresh(ctx,.8,True)
            if (audio is None or not audio.healthy()) and not visual_verified:
                return Result('failed','audio_disconnected',dict(casts=number+1,received=received))
            bobber=yield from ctx.work(template,s.frame,spec['bobber_template'],spec['world_roi'],.95)
            if not bobber:
                positioned=None
                continue
            # Position while waiting, not after the short-lived bite signal.
            if positioned is None or math_distance(positioned,bobber)>5:
                yield from move_pointer(ctx,cursor,bobber['x'],bobber['y'],tolerance=4)
                positioned=bobber
                continue
            sound=audio.since(cast_at,'bite') if audio and audio.healthy() else []
            sound=[e for e in sound if 0<=ctx.clock()-e.at<=.8
                   and getattr(e,'evidence',{}).get('window_start',e.at)>=cast_at
                   and e.identity not in ctx.checkpoint.get('consumed_bites',[])]
            visual=False
            if visual_verified and spec.get('visual_bite_template'):
                visual=bool((yield from ctx.work(template,s.frame,spec['visual_bite_template'],spec['world_roi'],.95)))
            if not sound and not visual:
                if (audio is None or not audio.healthy()) and not visual_verified:
                    return Result('failed','audio_disconnected',dict(casts=number+1,received=received))
                continue
            consumed=True  # One trigger per cast, including rejected outcomes.
            if sound:
                ctx.checkpoint['consumed_bites']=(ctx.checkpoint.get('consumed_bites',[])+[sound[-1].identity])[-100:]
            s=yield from fresh(ctx,.8,True)
            # Verify both bobber and pointer on a fresh frame before the click.
            current=yield from ctx.work(template,s.frame,spec['bobber_template'],spec['world_roi'],.95)
            if not current or math_distance(current,bobber)>10:
                break
            roi=(max(375,round(bobber['x'])-60),max(150,round(bobber['y'])-60),120,120)
            pointer=yield from ctx.work(cursor.cursor,s.frame,roi)
            if not pointer or math_distance(pointer,current)>8:
                break
            if sound and ctx.clock()-sound[-1].at>.8:
                break
            ctx.checkpoint['uncertain_transaction']=True
            bite=sound[-1] if sound else None
            generation=getattr(audio,'generation',None)
            def bite_valid():
                return (audio.healthy() and getattr(audio,'generation',None)==generation
                        and 0<=ctx.clock()-bite.at<=.8)
            yield from ctx.act(s,'click',(1,),'reel_fishing',.8,True,
                               expires_at=bite.at+.8 if bite else s.captured_at+.5,
                               admission_guard=bite_valid if bite else None)
            reel_sent=True
            ctx.record('fishing_reel',task=ctx.task,cast=number+1,event=sound[-1].identity if sound else 'visual',
                       event_at=sound[-1].at if sound else s.captured_at,frame=s.sequence)
            if (yield from receipt_confirmation(ctx,folder,spec['receipt_items'],before,6)):
                received+=1
                ctx.checkpoint['received']=received
            ctx.checkpoint['uncertain_transaction']=False
            break
        ctx.record('fishing_cast_finished',task=ctx.task,cast=number+1,trigger_consumed=consumed,
                   reel_sent=reel_sent,received=received)
        yield from ctx.pause(.5)
    return Result('completed','cast_limit',dict(casts=casts,received=received))


def math_distance(a,b):
    return ((a['x']-b['x'])**2+(a['y']-b['y'])**2)**.5
