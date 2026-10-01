"""Bounded, one-cast visual/audio calibration recording. No reel input."""
import base64,json,subprocess,threading,time,wave
from pathlib import Path
import cv2
from vision_feed import Feed
from kmbox_tap import KMBox
out=Path('runs')/('fishing-sample-'+time.strftime('%Y%m%d-%H%M%S'));out.mkdir()
Path('runs/current-fishing-sample.txt').write_text(str(out))
p=subprocess.Popen(['./audio_capture','--device',json.loads(Path('calibration/runtime.json').read_text())['audio']['device_uid']],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
chunks=[]
def collect():
 with (out/'audio.jsonl').open('w') as log:
  for line in p.stdout:
   r=json.loads(line)
   if r.get('pcm'):chunks.append(base64.b64decode(r.pop('pcm')))
   log.write(json.dumps(r)+'\n')
t=threading.Thread(target=collect);t.start()
f=Feed()
try:
 with KMBox() as b:
  im,_=f.raw_frame();cv2.imwrite(str(out/'before.jpg'),im)
  b.tap(37,80)
  start=time.monotonic();print(str(out.resolve()),flush=True)
  with (out/'frames.jsonl').open('w') as log:
   i=0
   while time.monotonic()-start<38:
    im,age=f.raw_frame();cv2.imwrite(str(out/f'{i:04}.jpg'),im)
    log.write(json.dumps(dict(i=i,t=time.monotonic()-start,at=time.monotonic(),age=age))+'\n');log.flush()
    cv2.imwrite(str(out/'latest.jpg'),im);i+=1;time.sleep(.05)
finally:
 f.close();p.terminate();p.wait(timeout=3);t.join(timeout=2)
 with wave.open(str(out/'audio.wav'),'wb') as w:
  w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b''.join(chunks))
 print('Recording complete',flush=True)
