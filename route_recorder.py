"""Independent latest-only coordinate OCR; records observed map coordinates."""
import csv
import json
from pathlib import Path
import queue
import re
import select
import subprocess
import threading
import time

import cv2


class RouteRecorder:
    def __init__(self, folder, roi, interval=1.0):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.roi = roi
        self.interval = interval
        self.next_sample = 0
        self.queue = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.points = []
        self.errors = 0
        self.worker = threading.Thread(target=self._run, name='coordinate-recorder', daemon=True)
        self.worker.start()

    def submit(self, frame, captured):
        now = time.monotonic()
        if now < self.next_sample:
            return
        self.next_sample = now + self.interval
        x,y,w,h = self.roi
        crop = frame[y:y+h,x:x+w].copy()
        if crop.shape[:2] != (h,w):
            return
        item = (time.time()-(now-captured),crop)
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                pass

    def _run(self):
        binary = Path(__file__).with_name('coordinate_ocr')
        proc = None
        try:
            proc = subprocess.Popen([str(binary.resolve())], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, bufsize=1)
            with (self.folder/'samples.jsonl').open('x') as log:
                while not self.stop_event.is_set() or not self.queue.empty():
                    try:
                        timestamp,crop = self.queue.get(timeout=.2)
                    except queue.Empty:
                        continue
                    image_path = self.folder/'latest-coordinate.png'
                    cv2.imwrite(str(image_path),cv2.resize(crop,None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC))
                    proc.stdin.write(str(image_path.resolve())+'\n');proc.stdin.flush()
                    if not select.select([proc.stdout],[],[],2)[0]:
                        raise TimeoutError('Coordinate OCR timed out')
                    result = json.loads(proc.stdout.readline())
                    text = ' '.join(item['text'] for item in result.get('items',[]))
                    confidence = min((item['confidence'] for item in result.get('items',[])),default=0)
                    values = re.findall(r'(?<!\d)\d{1,3}\.\d+(?!\d)',text)
                    row = {'timestamp':timestamp,'text':text,'confidence':confidence,
                           'ocr_ms':result.get('ocr_ms'),'accepted':False}
                    if len(values)==2 and confidence>=.6:
                        x,y=map(float,values)
                        plausible = 0<=x<=100 and 0<=y<=100
                        if plausible and self.points:
                            prev = self.points[-1]
                            dt = timestamp-prev['timestamp']
                            plausible = dt>0 and ((x-prev['x'])**2+(y-prev['y'])**2)**.5 <= min(.75,max(.25,dt*.5))
                        if plausible:
                            row.update(x=x,y=y,accepted=True)
                            self.points.append({'timestamp':timestamp,'x':x,'y':y})
                    log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
        except Exception as exc:
            self.errors += 1
            (self.folder/'error.txt').write_text(str(exc))
        finally:
            if proc:
                proc.terminate()
                try:proc.wait(timeout=1)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()

    def close(self):
        self.stop_event.set();self.worker.join(timeout=4)
        if self.worker.is_alive():
            raise TimeoutError('Coordinate worker did not stop')
        return self.export()

    def export(self):
        with (self.folder/'route.csv').open('w',newline='') as output:
            writer=csv.DictWriter(output,fieldnames=['timestamp','x','y'])
            writer.writeheader();writer.writerows(self.points)
        if self.points:
            xs=[p['x'] for p in self.points];ys=[p['y'] for p in self.points]
            x0,x1=min(xs)-.15,max(xs)+.15;y0,y1=min(ys)-.15,max(ys)+.15
            scale=min(640/(x1-x0),440/(y1-y0))
            coords=[(70+(x-x0)*scale,70+(y-y0)*scale) for x,y in zip(xs,ys)]
            points=' '.join(f'{x:.2f},{y:.2f}' for x,y in coords)
            first,last=coords[0],coords[-1]
            svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600">
<rect width="800" height="600" fill="#f8fafc"/>
<text x="40" y="32" font-family="sans-serif" font-size="20">Observed route — map coordinate units</text>
<polyline points="{points}" fill="none" stroke="#2563eb" stroke-width="3"/>
<circle cx="{first[0]}" cy="{first[1]}" r="6" fill="#16a34a"/>
<circle cx="{last[0]}" cy="{last[1]}" r="4" fill="#dc2626"/>
<text x="40" y="555" font-family="sans-serif" font-size="14">Start: {xs[0]:.1f}, {ys[0]:.1f} · End: {xs[-1]:.1f}, {ys[-1]:.1f} · Samples: {len(xs)}</text>
<text x="40" y="578" font-family="sans-serif" font-size="13">X increases right; Y increases down. No terrain or obstacle inference.</text>
</svg>'''
            (self.folder/'route.svg').write_text(svg)
        return {'coordinate_samples':len(self.points),'coordinate_errors':self.errors,
                'route_csv':str((self.folder/'route.csv').resolve())}
