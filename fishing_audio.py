"""Record-only USB audio with explicit host-clock conversion and short RMS windows.
No sound event authorizes input; thresholds require labeled scene recordings.
"""
import base64,ctypes,json,math,subprocess,threading,time,wave
from collections import deque
import numpy as np
from fishing_config import ROOT

class _Timebase(ctypes.Structure):
    _fields_=[('numer',ctypes.c_uint32),('denom',ctypes.c_uint32)]

def host_seconds():
    lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib');lib.mach_absolute_time.restype=ctypes.c_uint64
    info=_Timebase();lib.mach_timebase_info(ctypes.byref(info))
    return lib.mach_absolute_time()*info.numer/info.denom/1e9

class FishingAudio:
    def __init__(self,folder,uid):
        self.folder=folder;self.uid=uid;self.lock=threading.Lock();self.recent=deque(maxlen=400)
        self.error=None;self.ready=False;self.count=0;self.maximum=0.;self.stop=False
        # Python builds may expose a process-relative monotonic epoch. Measure
        # the host clock directly, never derive it from pipe arrival latency.
        start=time.monotonic();host=host_seconds();end=time.monotonic()
        self.host_to_python=(start+end)/2-host;self.clock_uncertainty=(end-start)/2
        self.wav=wave.open(str(folder/'audio.wav'),'wb');self.wav.setnchannels(1);self.wav.setsampwidth(2);self.wav.setframerate(16000)
        self.timeline=(folder/'audio-timeline.jsonl').open('w')
        self.proc=subprocess.Popen([str(ROOT/'audio_capture'),'--device',uid],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        self.worker=threading.Thread(target=self._run,daemon=True);self.worker.start()
    def _run(self):
        previous_end=None;sequence=0
        try:
            for line in self.proc.stdout:
                if self.stop:break
                r=json.loads(line)
                if 'error' in r:raise RuntimeError(r['error'])
                if r['kind']=='ready':
                    if self.ready or r['device_uid']!=self.uid or r['clock']!='mach_absolute_time':raise RuntimeError('Audio identity/clock mismatch')
                    self.ready=True;continue
                if not self.ready or r.get('rate')!=16000 or r.get('bits')!=16 or r.get('channels')!=1 or r.get('float') is not False or r.get('little_endian') is not True:raise RuntimeError('Audio PCM format mismatch')
                if r['sequence']!=sequence+1:raise RuntimeError('Audio sequence gap')
                sequence=r['sequence'];raw=base64.b64decode(r['pcm'],validate=True);pcm=np.frombuffer(raw,dtype='<i2').astype(float)/32768
                at=r['sample_time']+self.host_to_python;end=at+len(pcm)/16000;age=time.monotonic()-end
                if not -.02<=age<=.5:raise RuntimeError('Audio samples stale')
                if previous_end is not None and abs(at-previous_end)>.025:raise RuntimeError('Audio timeline gap')
                previous_end=end;rms=float(np.sqrt(np.mean(pcm**2)));peak=float(np.max(np.abs(pcm)))
                self.wav.writeframesraw(raw)
                self.timeline.write(json.dumps(dict(sequence=sequence,at=at,end=end,start_sample=self.count,samples=len(pcm),rms=rms,peak=peak,delivery_age=age))+'\n');self.timeline.flush()
                self.count+=len(pcm);self.maximum=max(self.maximum,rms)
                with self.lock:self.recent.append((at,end,pcm))
        except Exception as e:self.error=str(e)
    def window(self,at,seconds=.5):
        with self.lock:rows=[(a,b,p) for a,b,p in self.recent if b>=at-seconds and a<=at]
        if not rows:return dict(available=False,error=self.error)
        samples=[]
        for a,b,p in rows:
            lo=max(0,round((at-seconds-a)*16000));hi=min(len(p),round((at-a)*16000))
            if hi>lo:samples.append(p[lo:hi])
        if not samples:return dict(available=False,error=self.error)
        pcm=np.concatenate(samples);windows=[pcm[i:i+800] for i in range(0,len(pcm)-799,160)]
        return dict(available=self.error is None,age=at-rows[-1][1],rms=float(np.sqrt(np.mean(pcm**2))),peak=float(np.max(np.abs(pcm))),rms50_max=max((float(np.sqrt(np.mean(p*p))) for p in windows),default=0.),record_only=True)
    def close(self):
        self.stop=True;self.proc.terminate()
        try:self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        self.worker.join(timeout=2);self.wav.close();self.timeline.close();self.proc.stdout.close();self.proc.stderr.close()
        (self.folder/'audio-summary.json').write_text(json.dumps(dict(device_uid=self.uid,ready=self.ready,error=self.error,samples=self.count,seconds=self.count/16000,maximum_chunk_rms=self.maximum,host_to_python=self.host_to_python,clock_uncertainty_seconds=self.clock_uncertainty,record_only=True),indent=2))
