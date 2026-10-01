"""Strict local OCR of personal coin receipts, excluding channel chat."""
import re,json,subprocess
from difflib import SequenceMatcher
from pathlib import Path
from collections import Counter
import cv2
ROOT=Path(__file__).resolve().parent

def coin_counts(result):
    out=Counter()
    for item in result.get('items',[]):
        text=''.join(item.get('text','').split()).translate(str.maketrans('銅銀幣','铜银币'))
        if item.get('confidence',0)>=.5 and re.fullmatch(r'你拾取了(?:\d+[金银铜]币)+[。.]?',text):out[text]+=1
    return dict(out)

def loot_counts(result):
    out=Counter(coin_counts(result))
    for item in result.get('items',[]):
        text=''.join(item.get('text','').split())
        if item.get('confidence',0)>=.5 and re.fullmatch(r'你获得了战利品[:：].+',text):
            out[text]+=1
    return dict(out)


def receipt_rows(data, count_fn=coin_counts):
    rows=[]
    for item in data.get('items',[]):
        counts=count_fn({'items':[item]})
        if counts and 'box' in item:
            rows.append(dict(text=next(iter(counts)),y=item['box'][1]))
    return rows

def anchors(data):
    return [dict(text=''.join(i['text'].split()),y=i['box'][1]) for i in data.get('items',[])
            if 'box' in i and len(i.get('text',''))>=8 and not i['text'].startswith('你')]

def scroll_offset(before,after):
    matches=[]
    for i,a in enumerate(before.get('_anchors',[])):
        for j,b in enumerate(after.get('_anchors',[])):
            dy=b['y']-a['y']
            if 0<=dy<=.8 and SequenceMatcher(None,a['text'],b['text']).ratio()>=.8:
                matches.append((i,j,dy))
    offsets=[]
    for _,_,dy in matches:
        group=[(i,j) for i,j,d in matches if abs(d-dy)<.02]
        if len({i for i,j in group})>=2 and len({j for i,j in group})>=2:
            offsets.append(dy)
    if offsets and max(offsets)-min(offsets)<.025:return sum(offsets)/len(offsets)
    return None

def read_coin_receipts(frame,folder,label,include_items=False):
    folder=Path(folder);p=folder/(label+'.png')
    cv2.imwrite(str(p),cv2.resize(frame[670:830,398:778],None,fx=3,fy=3))
    r=subprocess.run([str(ROOT/'ui_ocr')],input=str(p.resolve())+'\n',text=True,capture_output=True,timeout=8,check=True)
    data=json.loads(r.stdout);(folder/(label+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    count_fn=loot_counts if include_items else coin_counts
    counts=count_fn(data);counts['_rows']=receipt_rows(data,count_fn);counts['_anchors']=anchors(data)
    return counts

def increased(before,after):return any(n>before.get(text,0) for text,n in after.items() if not text.startswith('_'))

def confirmed_pair(before,after):
    if len(after)!=2:return False
    if all(increased(before,r) for r in after):return True
    # If an old receipt fades between samples, require the new bottom receipt
    # established by the first count increase to persist at the same row.
    if not increased(before,after[0]):return False
    rows=after[0].get('_rows',[])
    if not rows:return False
    newest=min(rows,key=lambda r:r['y'])
    if after[0].get(newest['text'],0)<=before.get(newest['text'],0):return False
    offset=scroll_offset(after[0],after[1])
    if offset is None:offset=0
    return any(r['text']==newest['text'] and abs(r['y']-newest['y']-offset)<.025
               for r in after[1].get('_rows',[]))

def read_loot_receipts(frame,folder,label):
    return read_coin_receipts(frame,folder,label,include_items=True)
