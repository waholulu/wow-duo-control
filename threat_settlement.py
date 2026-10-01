"""Shared fresh-frame quiet confirmation, independent of total wait budget."""
POST_KILL_WAIT_SECONDS = 8


class QuietPeriod:
    def __init__(self, seconds=3, frames=3):
        self.seconds, self.frames = seconds, frames
        self.since = None
        self.count = 0
        self.last_sequence = None

    def step(self, now, sequence=None, *, valid=True, threat=False):
        if not valid or threat:
            self.since, self.count = None, 0
            self.last_sequence = sequence
            return False
        if sequence is not None and sequence == self.last_sequence:
            return False
        self.last_sequence = sequence
        if self.since is None:
            self.since = now
        self.count += 1
        return self.count >= self.frames and now-self.since >= self.seconds
