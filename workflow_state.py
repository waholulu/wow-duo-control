"""Session-only workflow evidence. No class-specific decisions or device input."""
import cv2
import numpy as np


class InventoryCache:
    def __init__(self, ttl=30):
        if isinstance(ttl,bool) or not isinstance(ttl,(int,float)) or not 1<=ttl<=300:
            raise ValueError('Inventory cache TTL must be 1..300 seconds')
        self.ttl=ttl
        self.invalidate()

    def invalidate(self):
        self.result=None
        self.at=None
        self.identity=None

    def remember(self, result, now, identity):
        self.invalidate()
        if result.status=='completed':
            self.result,self.at,self.identity=result,now,identity

    def get(self, now, identity):
        if self.at is None or not 0<=now-self.at<self.ttl or identity!=self.identity:
            self.invalidate()
        return self.result


class CorpseHint:
    """A search hint only. Fresh loot-cursor evidence still authorizes the click."""
    @staticmethod
    def scene(frame):
        # Terrain above the player, excluding the persistent chat and portraits.
        return cv2.resize(cv2.cvtColor(frame[280:430,870:1360],cv2.COLOR_BGR2GRAY),(49,15))

    def __init__(self,snapshot,point):
        self.point=point
        self.at=snapshot.captured_at
        self.calibration=(snapshot.calibration,snapshot.calibration_generation)
        self.background=self.scene(snapshot.frame)

    def points(self,snapshot,now):
        if (not 0<=now-self.at<=12 or
            self.calibration!=(snapshot.calibration,snapshot.calibration_generation) or
            np.abs(self.scene(snapshot.frame).astype(float)-self.background).mean()>8):
            return []
        x,y=self.point
        return [(int(x),int(y+d)) for d in (60,100,140)
                if 790<int(x)<1390 and 450<int(y+d)<875]
