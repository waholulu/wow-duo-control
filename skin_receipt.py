"""Local OCR fallback for the currently observed skinning material."""
import json
from pathlib import Path
import subprocess
import cv2

ROOT=Path(__file__).resolve().parent
SKIN_ITEMS=('破烂的皮革',)
RECEIPT_ITEMS=('破烂的皮革','破碎的爪子','断牙','破烂的毛皮')


def skin_counts(result):
    counts={name:0 for name in SKIN_ITEMS}
    order=[]
    for item in result.get('items',[]):
        text=''.join(item.get('text','').split())
        if item.get('confidence',0)<.3 or not text.startswith('你获得了'):
            continue
        for name in SKIN_ITEMS:
            if name in text:counts[name]+=1
        names=[name for name in RECEIPT_ITEMS if name in text]
        if len(names)==1:order.append(names[0])
    counts['_receipt_order']=order
    return counts


def read_skin_receipts(frame,folder,label):
    folder=Path(folder);path=folder/(label+'.png')
    crop=frame[670:830,398:778]
    enlarged=cv2.resize(crop,None,fx=3,fy=3)
    cv2.imwrite(str(path),enlarged)
    process=subprocess.run([str(ROOT/'ui_ocr')],input=str(path.resolve())+'\n',
                           text=True,capture_output=True,timeout=8,check=True)
    result=json.loads(process.stdout)
    (folder/(label+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    # Vision sometimes detects only the white item link and misses the green
    # receipt prefix. Re-read that single physical row, keeping the prefix gate.
    for index,item in enumerate(result.get('items',[])):
        text=''.join(item.get('text','').split())
        box=item.get('box')
        if text.startswith('你获得了') or not box or not any(n in text for n in RECEIPT_ITEMS):
            continue
        _,y,_,h=box
        height=enlarged.shape[0]
        top=max(0,int((1-y-h)*height)-3)
        bottom=min(height,int((1-y)*height)+4)
        row_path=folder/f'{label}-row-{index}.png'
        cv2.imwrite(str(row_path),enlarged[top:bottom])
        output=subprocess.run([str(ROOT/'ui_ocr')],input=str(row_path.resolve())+'\n',
                              text=True,capture_output=True,timeout=8,check=True)
        row=json.loads(output.stdout)
        (folder/f'{label}-row-{index}.json').write_text(json.dumps(row,ensure_ascii=False,indent=2))
        parts=row.get('items',[])
        joined=''.join(p.get('text','') for p in parts)
        if parts and joined.startswith('你获得了') and all(p.get('confidence',0)>=.3 for p in parts):
            item.update(text=joined,confidence=min(p['confidence'] for p in parts))
    return skin_counts(result)


def increased(before,after):
    if any(after.get(item,0)>before.get(item,0) for item in SKIN_ITEMS):return True
    old=before.get('_receipt_order',[]);new=after.get('_receipt_order',[])
    # Match retained receipts across scrolling, then inspect only appended ones.
    # No overlap means there is insufficient evidence, not permission to guess.
    for overlap in range(min(len(old),len(new)),0,-1):
        if old[-overlap:]==new[:overlap]:
            return any(item in SKIN_ITEMS for item in new[overlap:])
    return False
