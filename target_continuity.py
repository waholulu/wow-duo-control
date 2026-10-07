"""CV target identity continuity independent of unrelated resource recognition."""
class TargetContinuity:
    def __init__(self, aliases=None):
        self.aliases=aliases or {}
        self.previous=None
        self.number=0
        self.last_seen=float('-inf')

    def update(self, observation, label, calibration, generation, captured):
        readable=observation.valid or observation.reason=='status_text_unreadable'
        if readable and observation.target and observation.target_allowed:
            # Alternative visual templates are not entity identities. Only
            # explicit, identical name mappings may share a canonical label.
            names=self.aliases.get(label)
            canonical=tuple(sorted(set(names))) if names else label
            key=(calibration,generation,canonical)
            if (self.previous is None or self.previous[0]!=key
                    or observation.target_hp>self.previous[1]+.15):
                self.number+=1
            self.previous=(key,observation.target_hp)
            self.last_seen=captured
            return str(self.number)
        if captured-self.last_seen>1.2 or not readable or not observation.in_combat:
            self.previous=None
        return None
