"""Process-wide ownership lock for every script that can control the PC."""
from contextlib import contextmanager
from pathlib import Path
import fcntl


ROOT = Path(__file__).resolve().parent


@contextmanager
def controller_lock(root=ROOT):
    path = Path(root) / 'runs' / 'patrol-controller.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another controller is already running')
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
