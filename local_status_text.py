"""Small native status-text fallback. CV cache first; bounded local OCR only on change."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import threading
import cv2
import numpy as np

class PercentReader:
    def __init__(self, interval=.5, full_template=None):
        self.interval=interval
        self.full_template=cv2.imread(str(full_template)) if full_template else None
        self.last_at=float('-inf')
        self.previous=None
        self.value=None
        self.calls=0
        self.verified_patches=[]
        self.worker=None
        self.pending_result=None

    @staticmethod
    def parse(items):
        values=[]
        for item in items:
            text=item.get('text','').strip().replace('％','%').replace(' ','')
            match=re.fullmatch(r'(100|[0-9]{1,2})%',text)
            if match and item.get('confidence',0)>=.3:
                values.append(int(match[1])/100)
        return values[0] if len(values)==1 else None

    def read(self, patch):
        now=time.monotonic()
        if self.pending_result is not None:
            known,value=self.pending_result;self.pending_result=None
            if value is not None:
                self.previous=known;self.value=value
                self.verified_patches=(self.verified_patches+[(known,value)])[-16:]
        if self.full_template is not None:
            score=cv2.matchTemplate(patch,self.full_template,cv2.TM_CCOEFF_NORMED).max()
            if score>=.98:
                return 1.0
        # Reuse only a visually matching, previously OCR-confirmed text patch.
        # Compression/background shimmer must not turn unchanged digits unknown.
        for known,value in reversed(self.verified_patches):
            if patch.shape==known.shape and known.std()>2 and cv2.matchTemplate(patch,known,cv2.TM_CCOEFF_NORMED)[0,0]>=.975:
                return value
        if self.previous is not None and patch.shape==self.previous.shape:
            if np.abs(patch.astype(float)-self.previous.astype(float)).mean()<1:
                return self.value
        if now-self.last_at<self.interval:
            return None  # Never reuse a value after the visible text changes.
        if self.worker is not None and self.worker.is_alive():return None
        self.last_at=now
        self.calls+=1
        self.worker=threading.Thread(target=self.recognize,args=(patch.copy(),),daemon=True)
        self.worker.start()
        return None

    def recognize(self,patch):
        value=None
        try:
            with tempfile.TemporaryDirectory(prefix='wow-status-') as folder:
                path=Path(folder)/'percent.png'
                cv2.imwrite(str(path),cv2.resize(patch,None,fx=6,fy=6,interpolation=cv2.INTER_CUBIC))
                result=subprocess.run([str(Path(__file__).with_name('ui_ocr'))],input=str(path)+'\n',text=True,capture_output=True,timeout=.8,check=True)
                value=self.parse(json.loads(result.stdout)['items'])
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):
            pass
        self.pending_result=(patch,value)


def text_present(patch):
    hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
    return int(((hsv[:,:,1]<95)&(hsv[:,:,2]>130)).sum())>=8
