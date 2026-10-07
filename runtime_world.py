"""Local places, capability gates, and motion-tolerant minimap tracks."""
import hashlib
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Every module that can change an accepted live action or its safety decision.
# Keep this explicit so test/probe-only edits do not invalidate live evidence.
RUNTIME_SOURCE_FILES = (
    'runtime_types.py', 'runtime_engine.py', 'runtime_skills.py',
    'runtime_interactions.py', 'runtime_audio.py', 'runtime_safety.py',
    'runtime_store.py', 'runtime_main.py', 'runtime_world.py',
    'control_action.py', 'control_policy.py', 'resource_readiness.py',
    'timed_effects.py', 'melee_rotation.py', 'threat_settlement.py', 'threat_state.py',
    'offensive_effects.py', 'cast_cycle.py', 'run_permission.py',
    'workflow_state.py', 'target_continuity.py', 'combat_evidence.py', 'death_review.py',
    'class_profiles.py', 'interaction_vision.py', 'vision_state.py',
    'vision_feed.py', 'window_calibration.py', 'combat_log_cv.py',
    'combat_exit_watch.py', 'chat_tabs.py', 'coordinate_reader.py',
    'local_status_text.py', 'buff_timer.py', 'mineral_monitor.py',
    'navigate_local.py', 'navigation_coordinate.py', 'obs_probe.py',
    'route_recorder.py', 'stationary_gather.py',
    'coin_receipt.py', 'skin_receipt.py', 'kmbox_tap.py', 'control_lock.py',
)


def load_profile(path=None):
    path = Path(path or ROOT/'calibration/runtime.json')
    return json.loads(path.read_text())


def profile_digest(profile, extra_paths=()):
    digest=hashlib.sha256(json.dumps(profile, sort_keys=True).encode())
    # Acceptance is tied to detector calibration AND the executing code, not
    # just feature switches. Exclude the registry itself to avoid self-hashing.
    assets=[*(ROOT/name for name in RUNTIME_SOURCE_FILES),
            *sorted((ROOT/'classes').glob('*/combat.json')),
            *sorted((ROOT/'classes').glob('*/vision/*')),
            ROOT/'interaction_vision.py',ROOT/'vision_state.py',ROOT/'vision_feed.py',
            ROOT/'window_calibration.py',ROOT/'audio_capture.swift',
            *sorted((ROOT/'calibration').glob('*.png')),
            ROOT/'calibration/profile.json',ROOT/'calibration/window.json',
            ROOT/'calibration/places.json',*[Path(p).resolve() for p in extra_paths]]
    def referenced(value):
        if isinstance(value,dict):
            for item in value.values():referenced(item)
        elif isinstance(value,list):
            for item in value:referenced(item)
        elif isinstance(value,str) and Path(value).suffix.lower() in ('.png','.wav'):
            assets.append(ROOT/value)
    referenced(profile)
    for path in sorted(set(path.resolve() for path in assets)):
        label=str(path.relative_to(ROOT)) if ROOT in path.parents else str(path)
        digest.update(label.encode()+b'\0')
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b'<missing>')
    return digest.hexdigest()


class Capabilities:
    def __init__(self, profile, registry=None, trial=False, extra_paths=()):
        self.profile, self.trial = profile, trial
        self.registry = registry or {}
        self.extra_paths = tuple(extra_paths)

    def require(self, name):
        spec = self.profile.get(name, {})
        if spec.get('calibrated') is not True:
            raise ValueError(f'{name}: calibration_required')
        if not self.trial:
            proof = self.registry.get(name, {})
            if (proof.get('offline_passed') is not True or proof.get('live_passed') is not True
                    or proof.get('profile_sha256') != profile_digest(self.profile,self.extra_paths)):
                raise ValueError(f'{name}: acceptance_required (use bounded --trial for validation)')
        return spec


class Places:
    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text())

    def route(self, name, map_id):
        route = self.data['routes'][name]
        if not self.valid_map(map_id) or route['map'] != map_id or route.get('verified') is not True:
            raise ValueError('Route map mismatch or route not verified')
        points = route['points']
        if not isinstance(points,list) or len(points)<2 or any(not self.valid_point(p) for p in points):
            raise ValueError('Invalid route coordinates')
        return [tuple(p) for p in points]

    def place(self, name, map_id):
        p = self.data['places'][name]
        if not self.valid_map(map_id) or p['map'] != map_id:
            raise ValueError('Place map mismatch')
        if not self.valid_point(p.get('coordinate')):
            raise ValueError('Invalid place coordinates')
        return tuple(p['coordinate'])

    @staticmethod
    def valid_map(map_id):
        return isinstance(map_id,str) and bool(map_id.strip()) and map_id.strip()!='unconfirmed'

    @staticmethod
    def valid_point(point):
        return (isinstance(point,(list,tuple)) and len(point)==2
                and all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=100 for v in point))

    def remember(self, name, map_id, coordinate, source='user', evidence=None):
        if not isinstance(name,str) or not name.strip() or not self.valid_map(map_id):
            raise ValueError('A name and explicit map ID are required')
        if not self.valid_point(coordinate):
            raise ValueError('Coordinates must be finite and 0..100')
        from runtime_store import atomic_json
        value=dict(map=map_id,coordinate=list(coordinate),source=source,
                   recorded_at=time.time(),confirmed_at=time.time() if evidence else None,evidence=evidence)
        self.data.setdefault('places',{})[name]=value
        atomic_json(self.path,self.data)
        return value


class MineralTracker:
    """One-to-one nearest-neighbour tracks; tolerate bounded map movement.

    A track remains a candidate, never proof of a world node or permission to click.
    """
    def __init__(self):
        self.tracks = []
        self.next_id = 0
        self.last_at = None

    def update(self, points, now):
        if not math.isfinite(now) or self.last_at is not None and now<=self.last_at:
            # Duplicate processing of one observation cannot establish stability.
            return []
        self.last_at = now
        points=[p for p in points if isinstance(p,dict)
                and all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) for k in ('x','y'))]
        old = [p for p in self.tracks if now-p['at'] <= 1.2]
        used = set()
        out = []
        for p in points:
            possible = [(math.hypot(p['x']-v['x'], p['y']-v['y']), i, v)
                        for i,v in enumerate(old) if i not in used]
            possible.sort(key=lambda v:v[0])
            # Ambiguous crossing tracks start over rather than merge identities.
            if possible and possible[0][0]<=20 and (len(possible)==1 or possible[1][0]-possible[0][0]>4):
                _,i,previous = possible[0]
                used.add(i)
                track = dict(p, identity=previous['identity'], frames=previous['frames']+1,
                             first_at=previous['first_at'], at=now)
            else:
                self.next_id += 1
                track = dict(p, identity=f'mineral-{self.next_id}', frames=1, first_at=now, at=now)
            out.append(track)
        self.tracks = out
        return [p for p in out if p['frames']>=3]


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description='Remember a user-provided place without opening hardware')
    p.add_argument('--places',type=Path,default=ROOT/'calibration/places.json')
    p.add_argument('--name',required=True)
    p.add_argument('--map-id',required=True)
    p.add_argument('--coordinate',nargs=2,type=float,required=True)
    a=p.parse_args()
    value=Places(a.places).remember(a.name,a.map_id,a.coordinate)
    print(json.dumps(value,ensure_ascii=False))
