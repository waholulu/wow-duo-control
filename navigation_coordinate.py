"""Coordinate OCR consensus for the calibrated one-decimal minimap overlay."""
import json,re,subprocess
from collections import Counter
from pathlib import Path
import cv2
ROOT=Path(__file__).resolve().parent

def parse(result):
    parts=result.get('items',[])
    if not parts or min(p.get('confidence',0) for p in parts)<.9:return None
    text=' '.join(p['text'] for p in parts).strip()
    # The calibrated closing parenthesis is sometimes recognized as a zero.
    match=re.fullmatch(r'(\d{1,2}[.,:]\d)[ (\s]+(\d{1,2}[.,:]\d)[0)\s.]*',text)
    if not match:return None
    return tuple(float(v.replace(',', '.').replace(':', '.')) for v in match.groups())

def coordinate(frame,folder,index):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    crop=cv2.resize(frame[365:391,1490:1605],None,fx=4,fy=4)
    variants=[crop,cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),crop[:,:,1],cv2.resize(crop,None,fx=.5,fy=.5)]
    paths=[]
    for n,im in enumerate(variants):
        p=folder/f'coord-{index}-{n}.png';cv2.imwrite(str(p),im);paths.append(str(p.resolve()))
    output=subprocess.run([str(ROOT/'coordinate_ocr')],input='\n'.join(paths)+'\n',text=True,capture_output=True,timeout=6,check=True)
    rows=[json.loads(s) for s in output.stdout.splitlines()]
    (folder/f'coord-{index}.json').write_text(json.dumps(rows))
    counts=Counter(p for r in rows if (p:=parse(r)) is not None)
    if not counts or counts.most_common(1)[0][1]<3:
        return split_axes(frame,folder,index)
    return counts.most_common(1)[0][0]

def split_axes(frame,folder,index):
    paths=[]
    for axis,(left,widths) in enumerate([(1497,(33,34,35)),(1539,(28,29,30))]):
        for width in widths:
            im=cv2.resize(frame[370:386,left:left+width],None,fx=5,fy=5)
            gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
            for threshold in (150,170):
                bw=255-cv2.threshold(gray,threshold,255,cv2.THRESH_BINARY)[1]
                bw=cv2.copyMakeBorder(bw,15,15,15,15,cv2.BORDER_CONSTANT,value=255)
                p=folder/f'split-{index}-{axis}-{width}-{threshold}.png';cv2.imwrite(str(p),bw);paths.append(str(p.resolve()))
    output=subprocess.run([str(ROOT/'coordinate_ocr')],input='\n'.join(paths)+'\n',text=True,capture_output=True,timeout=6,check=True)
    rows=[json.loads(s) for s in output.stdout.splitlines()]
    (folder/f'split-{index}.json').write_text(json.dumps(rows))
    if len(rows)!=12:raise ValueError('Incomplete axis readings')
    position=[]
    for group in [rows[:6],rows[6:]]:
        values=[]
        for row in group:
            items=row.get('items',[])
            if len(items)!=1 or items[0].get('confidence',0)<.9:continue
            value=items[0]['text'].strip()
            if re.fullmatch(r'\d{1,2}[.,:]\d',value):values.append(float(value.replace(',','.').replace(':','.')))
        votes=Counter(values)
        if not votes or votes.most_common(1)[0][1]<3:raise ValueError('Coordinate axes disagree')
        position.append(votes.most_common(1)[0][0])
    return tuple(position)
