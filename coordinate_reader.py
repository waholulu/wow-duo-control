"""Calibrated coordinate OCR: isolate numeric axes from decorative parentheses."""
from pathlib import Path
import json
import re
import subprocess
from collections import Counter
import cv2

ROOT=Path(__file__).resolve().parent


def axis_consensus(readings):
    values=[]
    for result in readings:
        items=result.get('items',[])
        if len(items)!=1 or items[0].get('confidence',0)<.9:continue
        text=items[0]['text'].strip()
        if not re.fullmatch(r'\d{1,3}[.:]\d',text):continue
        value=float(text.replace(":","."))
        if 0<=value<=100:values.append(value)
    if not values:raise ValueError('No valid numeric axis')
    value,count=Counter(values).most_common(1)[0]
    if count<3:raise ValueError('Fewer than three agreeing axis readings')
    return value


def read_crop(enlarged,folder,index):
    folder=Path(folder);paths=[]
    for axis,(left,right) in enumerate([(46,151),(193,301)]):
        patch=enlarged[20:80,left:right]
        gray=cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY)
        for threshold in (175,180,185,190):
            bw=255-cv2.threshold(gray,threshold,255,cv2.THRESH_BINARY)[1]
            bw=cv2.copyMakeBorder(bw,16,16,16,16,cv2.BORDER_CONSTANT,value=255)
            path=folder/f'axis-{index}-{axis}-{threshold}.png';cv2.imwrite(str(path),bw);paths.append(str(path.resolve()))
    process=subprocess.run([str(ROOT/'coordinate_ocr')],input='\n'.join(paths)+'\n',
                           text=True,capture_output=True,timeout=6,check=True)
    readings=[json.loads(line) for line in process.stdout.splitlines()]
    (folder/f'axis-{index}.json').write_text(json.dumps(readings,indent=2))
    if len(readings)!=8:raise ValueError('Incomplete coordinate OCR response')
    return [axis_consensus(readings[:4]),axis_consensus(readings[4:])]


def read_coordinate(frame,folder,index):
    if frame.shape[:2]!=(1080,1920):raise ValueError('Uncalibrated frame size')
    crop=cv2.resize(frame[365:391,1490:1605],None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)
    return read_crop(crop,folder,index)
