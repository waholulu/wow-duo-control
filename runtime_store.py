"""Bounded evidence recording and atomic terminal results, off the control path."""
from collections import deque
from dataclasses import asdict
import json
import uuid
import hashlib
import base64
import math
from pathlib import Path
import queue
import threading
import time
import cv2


def window_covered(intervals, start, end, tolerance):
    """Coverage comes from captured samples, never the writer's wall clock."""
    intervals=sorted((a,b) for a,b in intervals if b>=start and a<=end)
    if not intervals or intervals[0][0]>start+tolerance:
        return False
    cursor=start
    for left,right in intervals:
        if left>cursor+tolerance:
            return False
        cursor=max(cursor,right)
    return cursor>=end-tolerance


def audio_interval(row):
    try:
        size=len(base64.b64decode(row['pcm'],validate=True))
        if (not size or any(type(row[k]) not in (int,float) or not math.isfinite(row[k]) or row[k]<=0
                            for k in ('rate','channels','bits')) or row['bits']%8
                or size%(row['channels']*(row['bits']/8))):
            return None
        duration=size/(row['rate']*row['channels']*(row['bits']/8))
        return row['at'],row['at']+duration
    except (ValueError,KeyError,TypeError,ZeroDivisionError):
        return None


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    temporary.replace(path)


class Store:
    def __init__(self, folder, config, max_clips=100, max_bytes=2_000_000_000):
        if type(max_clips) is not int or max_clips<1 or type(max_bytes) is not int or max_bytes<1:
            raise ValueError('Recording limits must be positive integers')
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=False)
        atomic_json(self.folder/'config.json', config)
        root=Path(__file__).resolve().parent
        sources=sorted([*root.glob('*.py'),*root.glob('*.swift')])
        digest=hashlib.sha256()
        for source in sources:
            digest.update(source.name.encode()+b'\0'+source.read_bytes())
        atomic_json(self.folder/'run.json',dict(started_wall_time=time.time(),
            started_monotonic=time.monotonic(),source_sha256=digest.hexdigest()))
        self.queue = queue.Queue(maxsize=64)
        self.dropped = 0
        self.error = None
        self.stopping = threading.Event()
        self.frames = deque(maxlen=48)
        self.audio = deque(maxlen=2048)
        self.pending = []
        self.clips = deque()
        self.max_clips = max_clips
        self.skipped_clips = 0
        self.evicted_clips = 0
        self.event_rotations = 0
        self.max_bytes = max_bytes
        self.recorded_bytes = 0
        self.audit_actions = bool(config.get('audit_actions', False))
        self.audit_frames_recorded = 0
        if self.audit_actions:
            (self.folder/'action-frames').mkdir()
        self.next_budget_check = 0
        self.worker = threading.Thread(target=self._run, name='evidence', daemon=True)
        self.worker.start()

    def emit(self, kind, **values):
        row = dict(schema_version=1, at=time.monotonic(), kind=kind, **values)
        try:
            self.queue.put_nowait(('event', row))
        except queue.Full:
            self.dropped += 1
            self.error = 'event_queue_overflow'
        return row

    def frame(self, snapshot):
        try:
            self.queue.put_nowait(('frame', snapshot))
        except queue.Full:
            self.dropped += 1

    def audio_chunk(self, row):
        try:
            self.queue.put_nowait(('audio', row))
        except queue.Full:
            self.dropped += 1

    def clip(self, reason):
        self.emit('evidence_request', reason=reason)

    def _check_budget(self, additional=0):
        # Include skill OCR artifacts, not just the recorder's own clip files.
        # Never prune fixtures or silently erase result evidence to keep running.
        total=0
        for path in self.folder.rglob('*'):
            try:
                if path.is_file():total+=path.stat().st_size
            except FileNotFoundError:
                # Atomic status replacement can rename a temporary between
                # enumeration and stat; this is not a recording failure.
                continue
        self.recorded_bytes=total
        if self.recorded_bytes+additional>self.max_bytes:
            self.error='recording_disk_budget_exceeded'
            return False
        return True

    def _finish_clip(self, clip, interrupted=False):
        folder = self.folder/'evidence'/clip['id']
        folder.mkdir(parents=True, exist_ok=True)
        start,end=clip['at']-5,clip['until']
        frames=[r for r in clip.pop('frames') if start<=r[1]<=end]
        chunks = [r for r in clip.pop('audio') if start<=r['at']<=end]
        estimated=sum(len(row[2]) for row in frames)+sum(len(json.dumps(row))+1 for row in chunks)
        if not self._check_budget(estimated):
            frames,chunks=[],[]
            clip['missing_reason']='recording_disk_budget_exceeded'
        for seq, at, encoded in frames:
            (folder/f'{seq:08d}.jpg').write_bytes(encoded)
        with (folder/'audio.jsonl').open('w') as stream:
            for chunk in chunks:
                stream.write(json.dumps(chunk)+'\n')
        videos=[(r[1],r[1]) for r in frames]
        audios=[audio_interval(r) for r in chunks]
        synchronized=bool(chunks) and None not in audios and all(r.get('synchronized') is True for r in chunks)
        audios=[interval for interval in audios if interval is not None]
        audio_pre=synchronized and window_covered(audios,start,clip['at'],.1)
        audio_post=synchronized and not interrupted and window_covered(audios,clip['at'],end,.1)
        video_pre=window_covered(videos,start,clip['at'],.25)
        video_post=not interrupted and window_covered(videos,clip['at'],end,.25)
        clip.update(audio_available=bool(chunks), video_available=bool(frames),
                    audio_synchronized=synchronized,
                    frame_times=[[r[0],r[1]] for r in frames],
                    video_pre_complete=video_pre,video_post_complete=video_post,
                    audio_pre_complete=audio_pre,audio_post_complete=audio_post,
                    pre_window_complete=video_pre and audio_pre,
                    post_window_complete=video_post and audio_post,
                    stopped_before_post_window=interrupted,dropped_records=self.dropped)
        atomic_json(folder/'manifest.json', clip)
        self.clips.append(folder)
        if len(self.clips) > self.max_clips:
            import shutil
            shutil.rmtree(self.clips.popleft())
            self.evicted_clips+=1

    def _run(self):
        try:
            path = self.folder/'events.jsonl'
            stream = path.open('a')
            try:
                while not self.stopping.is_set() or not self.queue.empty():
                    try:
                        kind, value = self.queue.get(timeout=.05)
                    except queue.Empty:
                        kind, value = None, None
                    now = time.monotonic()
                    if now>=self.next_budget_check:
                        self._check_budget()
                        self.next_budget_check=now+1
                    if kind == 'frame':
                        ok, encoded = cv2.imencode('.jpg', value.frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                        if ok:
                            row = (value.sequence, value.captured_at, encoded.tobytes())
                            if self.audit_actions and not self.error:
                                try:
                                    (self.folder/'action-frames'/f'{value.sequence:08d}.jpg').write_bytes(row[2])
                                    self.audit_frames_recorded += 1
                                except OSError as exc:
                                    self.error='action_frame_write_failed: '+str(exc)
                            self.frames.append(row)
                            for clip in self.pending:
                                clip['frames'].append(row)
                                clip['frame_times'].append([row[0], row[1]])
                        else:
                            self.dropped+=1
                            self.error='frame_encoding_failed'
                    elif kind == 'audio':
                        self.audio.append(value)
                        while self.audio and self.audio[0]['at']<value['at']-5:
                            self.audio.popleft()
                        for clip in self.pending:
                            clip['audio'].append(value)
                    elif kind == 'event':
                        stream.write(json.dumps(value, ensure_ascii=False, default=str)+'\n')
                        stream.flush()
                        if stream.tell() >= 8_000_000:
                            stream.close()
                            path.replace(self.folder/'events.previous.jsonl')
                            self.event_rotations+=1
                            stream = path.open('a')
                        request=(value['kind']=='evidence_request' or value['kind']=='action_sent' and value.get('action')=='click')
                        if request and len(self.pending)>=4:
                            self.skipped_clips+=1
                        if request and len(self.pending) < 4:
                            frames = [r for r in self.frames if r[1] >= value['at']-5]
                            chunks = [r for r in self.audio if r['at'] >= value['at']-5]
                            self.pending.append(dict(id=f"{time.time_ns()}", reason=value.get('reason',value['kind']),
                                at=value['at'], until=value['at']+10, frames=list(frames), audio=chunks,
                                frame_times=[[r[0],r[1]] for r in frames],
                                pre_window_complete=bool(frames and frames[0][1] <= value['at']-4.8)))
                    for clip in self.pending[:]:
                        if now >= clip['until']:
                            self._finish_clip(clip)
                            self.pending.remove(clip)
                for clip in self.pending:
                    self._finish_clip(clip, interrupted=True)
            finally:
                stream.close()
        except Exception as exc:
            self.error = str(exc)

    def status(self, **value):
        atomic_json(self.folder/'status.json', dict(schema_version=1, **value))

    def result(self, result):
        atomic_json(self.folder/'result.json', dict(schema_version=1, **asdict(result)))

    def close(self):
        self.stopping.set()
        self.worker.join(timeout=5)
        self._check_budget()
        atomic_json(self.folder/'recording.json', dict(dropped_records=self.dropped,
            audit_frames_recorded=self.audit_frames_recorded,
            skipped_clip_requests=self.skipped_clips,evicted_clips=self.evicted_clips,
            event_rotations=self.event_rotations,max_bytes=self.max_bytes,recorded_bytes=self.recorded_bytes,
            error=self.error, worker_stopped=not self.worker.is_alive()))
