"""Local authenticated OBS frames for CV; credentials never enter logs."""
import argparse
import base64
import json
import os
from pathlib import Path
import time
import threading

import cv2
import numpy as np

from obs_probe import OBS
from window_calibration import WindowCalibration


class Feed:
    def __init__(self, source='Video Capture Device'):
        config_path = Path.home() / 'Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json'
        config = json.loads(config_path.read_text())
        password = os.environ.get('OBS_PASSWORD', config.get('server_password', ''))
        port = config.get('server_port', 4455)
        self.obs = OBS(f'ws://127.0.0.1:{port}', password)
        self.source = source
        self.calibration = WindowCalibration()
        self.calibration_checked_at = 0.0

    def raw_frame(self):
        started = time.monotonic()
        result = self.obs.request('GetSourceScreenshot', {
            'sourceName': self.source, 'imageFormat': 'jpg', 'imageCompressionQuality': 80,
        })
        encoded = result['imageData'].split(',', 1)[1]
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('OBS returned an undecodable frame')
        return frame, time.monotonic() - started

    def frame(self):
        deadline=time.monotonic()+8
        while True:
            frame,elapsed=self.raw_frame()
            processing=time.monotonic()
            normalized=self.calibration.normalize(frame)
            if normalized is not None:
                if getattr(self,'logged_transform',None)!=self.calibration.transform:
                    status=Path(__file__).resolve().parent/'runs/window-calibration.json'
                    status.parent.mkdir(exist_ok=True)
                    status.write_text(json.dumps(dict(at=time.time(),raw_size=[frame.shape[1],frame.shape[0]],**self.calibration.status()),indent=2))
                    self.logged_transform=self.calibration.transform
                self.calibration_checked_at=time.monotonic()
                return normalized,elapsed+time.monotonic()-processing
            # Discard every frame used for the slower global search. A newly
            # captured and independently validated frame is required for input.
            if time.monotonic()>=deadline:
                self.calibration_checked_at=0
                raise RuntimeError('Window calibration unavailable: black, occluded or unsupported layout')
            if not (frame.std()>2 and self.calibration.acquire(frame)):
                time.sleep(.1)

    def mouse_delta(self,dx,dy):
        if time.monotonic()-self.calibration_checked_at>.5:
            raise RuntimeError('Window calibration stale before mouse movement')
        return self.calibration.mouse_delta(dx,dy)

    def close(self):
        self.obs.close()


class LatestFrames:
    """Single-slot observation buffer: no stale frame queue or model calls."""
    def __init__(self, feed, hz=8):
        if not 0 < hz <= 30:
            raise ValueError('Capture rate must be in (0, 30]')
        self.feed = feed
        self.period = 1 / hz
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.latest = None
        self.error = None
        self.sequence = 0
        self.worker = threading.Thread(target=self._run, name='obs-latest-frame', daemon=True)
        self.worker.start()

    def _run(self):
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                frame, elapsed = self.feed.frame()
                with self.condition:
                    self.sequence += 1
                    # Timestamp request start conservatively; source latency is extra.
                    self.latest = (self.sequence, time.monotonic()-elapsed, frame, elapsed)
                    self.condition.notify_all()
                self.stop_event.wait(max(0, self.period - (time.monotonic() - started)))
        except Exception as exc:
            with self.condition:
                self.error = exc
                self.condition.notify_all()
        finally:
            self.feed.close()

    def get(self, after=0, timeout=10, max_age=0.5):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                if self.error is not None:
                    raise RuntimeError('Capture failed; stop input') from self.error
                if self.stop_event.is_set():
                    raise RuntimeError('Capture stopped')
                if self.latest is not None:
                    sequence, started, frame, elapsed = self.latest
                    if sequence > after and time.monotonic() - started <= max_age:
                        return sequence, started, frame, elapsed
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('No fresh observation; stop input')
                self.condition.wait(remaining)

    def close(self):
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        self.worker.join(timeout=6)
        if self.worker.is_alive():
            raise TimeoutError('Capture worker did not stop')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--raw', action='store_true', help='Diagnostic capture without window normalization')
    args = parser.parse_args()
    feed = Feed()
    timings = []
    try:
        for _ in range(args.samples):
            frame, elapsed = feed.raw_frame() if args.raw else feed.frame()
            timings.append(elapsed * 1000)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.output), frame):
            raise OSError('Failed saving observation')
        print(json.dumps({'width': frame.shape[1], 'height': frame.shape[0],
                          'request_ms_p50': float(np.median(timings)),
                          'request_ms_max': max(timings),
                          'samples': len(timings), 'output': str(args.output.resolve())}))
    finally:
        feed.close()


if __name__ == '__main__':
    main()
