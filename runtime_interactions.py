"""Profile-gated mining and merchant skills; receipts authorize success."""
from collections import Counter
import json
import math
from pathlib import Path
import re
import subprocess
import cv2
import numpy as np
from interaction_vision import LootVision
from runtime_types import Result, GuardFailed
from runtime_skills import fresh, tap, move_pointer, location, navigate

ROOT=Path(__file__).resolve().parent


def crop(frame, roi):
    if not roi or len(roi)!=4:
        raise ValueError('Missing calibrated ROI')
    x,y,w,h=map(int,roi)
    if x<0 or y<0 or w<1 or h<1 or x+w>frame.shape[1] or y+h>frame.shape[0]:
        raise ValueError('ROI outside frame')
    return frame[y:y+h,x:x+w]


def template(frame, path, roi, threshold=.95):
    if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not .5<=threshold<=1:
        raise ValueError('Invalid template threshold')
    im=cv2.imread(str(ROOT/path))
    if im is None or im.std()<2:
        raise ValueError('Missing or featureless interaction template')
    patch=crop(frame,roi)
    if im.shape[0]>patch.shape[0] or im.shape[1]>patch.shape[1]:
        raise ValueError('Template exceeds ROI')
    score=cv2.matchTemplate(patch,im,cv2.TM_CCOEFF_NORMED)
    _,value,_,(x,y)=cv2.minMaxLoc(score)
    if not math.isfinite(value) or value<threshold:
        return None
    return dict(x=roi[0]+x+im.shape[1]/2,y=roi[1]+y+im.shape[0]/2,score=value)


def ocr(frame, roi, folder, label):
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    p=folder/(label+'.png')
    if not cv2.imwrite(str(p),cv2.resize(crop(frame,roi),None,fx=3,fy=3)):
        raise OSError('Cannot save OCR evidence')
    run=subprocess.run([str(ROOT/'ui_ocr')],input=str(p.resolve())+'\n',text=True,
                       capture_output=True,timeout=8,check=True)
    data=json.loads(run.stdout)
    (folder/(label+'.json')).write_text(json.dumps(data,ensure_ascii=False))
    return data


def personal_receipts(data, names, minimum_confidence=.8):
    # Strict personal receipt; unrelated player chat and bare item links fail.
    counts=Counter()
    for item in data.get('items',[]):
        text=''.join(item.get('text','').split())
        if item.get('confidence',0)<minimum_confidence:
            continue
        for name in names:
            if re.fullmatch(r'你(?:获得|拾取)了(?:(?:战利品|物品)[:：])?[:：]?\[?'+re.escape(name)+r'\]?(?:[x×]\d+)?[。.]?',text):
                counts[name]+=1
    return dict(counts)


def receipts_increased(before, after):
    return any(count>before.get(name,0) for name,count in after.items())


def read_receipts(ctx, folder, names, label, roi=(398,670,380,160), minimum_confidence=.8):
    s=yield from fresh(ctx,.8,True)
    data=yield from ctx.work(ocr,s.frame,roi,folder,f'{label}-{s.sequence}')
    return personal_receipts(data,names,minimum_confidence)


def receipt_confirmation(ctx, folder, names, before, seconds=12, roi=(398,670,380,160), minimum_confidence=.8):
    until=min(ctx.deadline,ctx.clock()+seconds)
    confirmed=0
    while ctx.clock()<until:
        after=yield from read_receipts(ctx,folder,names,'receipt-after',roi,minimum_confidence)
        confirmed=confirmed+1 if receipts_increased(before,after) else 0
        if confirmed>=2:
            return True
        yield from ctx.pause(.2)
    return False


def mining(ctx, scheduler, spec, candidate, folder, return_point=None):
    """One node; reserve at least 30 seconds of the 90s detour for returning."""
    vision=LootVision()
    mine=cv2.imread(str(ROOT/spec['cursor_template']))
    if mine is None:
        raise GuardFailed('mining_cursor_uncalibrated')
    vision.templates['mine']=mine
    node_deadline=min(ctx.deadline-30,ctx.clock()+60)
    ctx.phase='mining'
    origin=return_point or (yield from location(ctx,folder))
    ctx.checkpoint['mining_return']=dict(position=origin,deadline=ctx.deadline)
    outcome=Result('skipped','mining_budget')
    try:
        found=yield from move_pointer(ctx,vision,candidate['x'],candidate['y'],tolerance=3)
        labels=[]
        for _ in range(2):
            s=yield from fresh(ctx,.95,True,True)
            raw=yield from ctx.work(ocr,s.frame,(1395,190,245,690),folder,f'mineral-name-{s.sequence}')
            labels.append({''.join(i['text'].split()) for i in raw.get('items',[]) if i.get('confidence',0)>=.8} & set(spec['names']))
        if not labels[0] or not labels[0]&labels[1]:
            ctx.checkpoint.pop('mining_return',None)
            return Result('skipped','mineral_name_unconfirmed')
        # Hovering can cover the tracked dot. Move away and reacquire while
        # stationary; never confuse a new arbitrary dot with the same node.
        yield from move_pointer(ctx,vision,800,250,expected='hand')
        yield from ctx.pause(1.6)
        nearby=[p for p in scheduler.source.minerals
                if math.hypot(p['x']-candidate['x'],p['y']-candidate['y'])<=10
                and ctx.clock()-p['at']<=1.2]
        if len(nearby)!=1:
            ctx.checkpoint.pop('mining_return',None)
            return Result('skipped','mineral_marker_not_reacquired')
        candidate=nearby[0]
        heading=None
        marker=candidate
        for _ in range(18):
            if ctx.clock()+1.2>=node_deadline:
                break
            s=yield from fresh(ctx,.95,True,True)
            candidates=[p for p in scheduler.source.minerals if p['identity']==candidate['identity'] and ctx.clock()-p['at']<=1.2]
            if len(candidates)!=1:
                outcome=Result('skipped','mineral_marker_lost')
                break
            marker=candidates[0]
            if math.hypot(marker['x']-1535,marker['y']-280)<=18:
                outcome=yield from mine_world_node(ctx,vision,spec,folder,node_deadline)
                break
            if heading is not None:
                desired=math.atan2(marker['y']-280,marker['x']-1535)
                error=(desired-heading+math.pi)%(2*math.pi)-math.pi
                if abs(error)>.3:
                    yield from tap(ctx,79 if error>0 else 80,min(200,max(40,round(abs(error)/math.pi*1000))),
                                   'mining_turn',.95,True)
            old=marker
            yield from tap(ctx,26,500,'mining_approach',.95,True)
            yield from ctx.pause(.5)
            candidates=[p for p in scheduler.source.minerals if p['identity']==candidate['identity']]
            if len(candidates)!=1:
                outcome=Result('skipped','mineral_marker_lost')
                break
            dx,dy=old['x']-candidates[0]['x'],old['y']-candidates[0]['y']
            if math.hypot(dx,dy)<.7:
                outcome=Result('skipped','mineral_approach_obstructed')
                break
            heading=math.atan2(dy,dx)
    except GuardFailed as exc:
        outcome=Result('skipped',str(exc))
    # Explicit return is part of the transaction, including after a failed mine.
    ctx.phase='mining_return'
    returned=yield from navigate(ctx,folder,[origin],checkpoint_key='mining_waypoint')
    if returned.status!='completed':
        return Result('failed','mining_return_failed',dict(mining=outcome.reason,return_reason=returned.reason))
    ctx.checkpoint.pop('mining_return',None)
    ctx.checkpoint.pop('mining_waypoint',None)
    return Result(outcome.status,outcome.reason,dict(outcome.facts,returned=True),outcome.evidence)


def mine_world_node(ctx, vision, spec, folder, deadline):
    s=yield from fresh(ctx,.95,True,True)
    points=[]
    for item in spec['node_templates']:
        p=yield from ctx.work(template,s.frame,item['file'],item['roi'],item.get('threshold',.95))
        if p:
            points.append(p)
    for p in points[:6]:
        if ctx.clock()>=deadline:
            break
        found=yield from move_pointer(ctx,vision,p['x'],p['y'],expected='mine')
        if not found:
            continue
        before=yield from read_receipts(ctx,folder,spec['receipt_items'],'mining-before')
        found=yield from move_pointer(ctx,vision,p['x'],p['y'],expected='mine')
        if not found:
            return Result('skipped','mining_cursor_changed')
        s,_=found
        if ctx.clock()>=deadline:
            return Result('skipped','mining_budget_before_click')
        ctx.checkpoint['uncertain_transaction']=True
        yield from ctx.act(s,'click',(1,),'mine_node',.95,True)
        if (yield from receipt_confirmation(ctx,folder,spec['receipt_items'],before,min(12,max(0,deadline-ctx.clock())))):
            ctx.checkpoint['uncertain_transaction']=False
            return Result('completed','mineral_receipt_confirmed',dict(mineral_received=True),(str(folder),))
        return Result('skipped','mineral_receipt_unconfirmed')
    return Result('skipped','world_node_not_located')


def money(data):
    items=data.get('items',[])
    if not items or any(i.get('confidence',0)<.9 for i in items):
        return None
    text=''.join(''.join(i['text'].split()) for i in items)
    if not re.fullmatch(r'(?:\d+金币?)?(?:\d+银币?)?(?:\d+铜币?)?',text) or not text:
        return None
    return sum(int(n)*{'金':10000,'银':100,'铜':1}[unit] for n,unit in re.findall(r'(\d+)([金银铜])',text))


def sale_allowed(item, tooltip):
    if item.get('category')!='junk' or item.get('quality')!='poor' or item.get('approved') is not True:
        return False
    entries=tooltip.get('items',[])
    if not entries or any(type(i.get('confidence')) not in (int,float) or
                          not .9<=i['confidence']<=1 for i in entries):
        return False
    texts=[''.join(i['text'].split()) for i in entries]
    forbidden=('任务物品','任务道具','装备后绑定','灵魂绑定','需要等级','材料','武器','护甲')
    return item['name'] in texts and not any(word in t for t in texts for word in forbidden)


def sale_frame_unchanged(before, after, slot, spec):
    """The exact tooltip and slot that authorized a sale must still be visible."""
    return all(np.array_equal(crop(before,roi),crop(after,roi))
               for roi in (slot['roi'],spec['item_tooltip_roi']))


def sale_send_guard(ctx, authorized, slot, spec):
    """Fast final write admission: no OCR or template search under input lock."""
    current=ctx.current_snapshot() if ctx.current_snapshot else None
    if current is None:
        return False
    x,y=slot['point']
    cursor_roi=(x-35,y-35,70,70)
    return sale_frame_unchanged(authorized.frame,current.frame,slot,spec) and all(
        np.array_equal(crop(authorized.frame,roi),crop(current.frame,roi))
        for roi in (spec['window_roi'],cursor_roi))


def vendor_window(ctx,spec):
    s=yield from fresh(ctx,.8,True)
    if not (yield from ctx.work(template,s.frame,spec['window_template'],spec['window_roi'])):
        raise GuardFailed('merchant_window_not_confirmed')
    return s


def read_money(ctx,spec,folder):
    values=[]
    for _ in range(2):
        s=yield from vendor_window(ctx,spec)
        raw=yield from ctx.work(ocr,s.frame,spec['money_roi'],folder,f'money-{s.sequence}')
        values.append(money(raw))
    if values[0] is None or values[0]!=values[1]:
        raise GuardFailed('money_unconfirmed')
    return values[0]


def close_merchant(ctx,spec):
    """Do not navigate with a merchant dialog that has not been dismissed."""
    s=yield from fresh(ctx,.8,True)
    present=yield from ctx.work(template,s.frame,spec['window_template'],spec['window_roi'])
    if present:
        yield from tap(ctx,41,80,'close_merchant',.8,True)
    evidence=[]
    for _ in range(2):
        s=yield from fresh(ctx,.8,True)
        if (yield from ctx.work(template,s.frame,spec['window_template'],spec['window_roi'])):
            return Result('failed','merchant_closure_unconfirmed')
        evidence.append(s.sequence)
    return Result('completed','merchant_closed',{},tuple(evidence))


def sell_junk(ctx,spec,folder):
    ctx.phase='vendor'
    cursor=LootVision()
    opened=False
    for x,y in spec.get('merchant_points',[(1000,480),(950,480),(1050,480),(900,560),(1100,560),(1000,630)]):
        yield from move_pointer(ctx,cursor,x,y)
        confirmed=0
        for _ in range(2):
            s=yield from fresh(ctx,.8,True)
            raw=yield from ctx.work(ocr,s.frame,spec.get('merchant_tooltip_roi',(1390,740,250,150)),folder,f'vendor-{s.sequence}')
            confirmed+=any(spec['name']==''.join(i['text'].split()) and i.get('confidence',0)>=.9 for i in raw.get('items',[]))
        if confirmed==2:
            found=yield from move_pointer(ctx,cursor,x,y)
            s,_=found
            yield from ctx.act(s,'click',(1,),'open_merchant',.8,True)
            yield from ctx.pause(.5)
            yield from vendor_window(ctx,spec)
            opened=True
            break
    if not opened:
        return Result('failed','merchant_not_confirmed')
    initial=yield from read_money(ctx,spec,folder)
    sold=[]
    # Re-scan after every sale; no cached list of clickable slot positions.
    for _ in range(len(spec['slots'])):
        candidate=None
        for slot in spec['slots']:
            s=yield from vendor_window(ctx,spec)
            matches=[]
            for item in spec['allowed_junk']:
                if item.get('category')!='junk' or item.get('quality')!='poor' or item.get('approved') is not True:
                    continue
                if (yield from ctx.work(template,s.frame,item['icon'],slot['roi'],.97)):
                    matches.append(item)
            if len(matches)!=1:
                continue
            item=matches[0]
            x,y=slot['point']
            yield from move_pointer(ctx,cursor,x,y,bounds=(375,150,1645,940))
            valid=True
            for _ in range(2):
                s=yield from vendor_window(ctx,spec)
                data=yield from ctx.work(ocr,s.frame,spec['item_tooltip_roi'],folder,f'item-{s.sequence}')
                valid=valid and sale_allowed(item,data)
            if valid:
                candidate=(slot,item,s)
                break
        if candidate is None:
            break
        slot,item,identified=candidate
        before_money=yield from read_money(ctx,spec,folder)
        yield from move_pointer(ctx,cursor,*slot['point'],bounds=(375,150,1645,940))
        s=yield from vendor_window(ctx,spec)
        if not (yield from ctx.work(template,s.frame,item['icon'],slot['roi'],.97)):
            return Result('failed','item_changed_before_sale',dict(sold=sold))
        position=yield from ctx.work(cursor.cursor,s.frame)
        if (not position or abs(position['x']-slot['point'][0])>8 or abs(position['y']-slot['point'][1])>8 or
                not sale_frame_unchanged(identified.frame,s.frame,slot,spec)):
            return Result('failed','sale_identity_or_pointer_changed',dict(sold=sold))
        ctx.checkpoint['uncertain_transaction']=True
        yield from ctx.act(s,'click',(1,),'sell_approved_gray_junk',.8,True,
                           admission_guard=lambda:sale_send_guard(ctx,s,slot,spec))
        yield from ctx.pause(.3)
        after_money=yield from read_money(ctx,spec,folder)
        # A sold stack must leave an empty slot, not merely change its icon.
        clear=0
        for _ in range(2):
            s=yield from vendor_window(ctx,spec)
            clear+=bool((yield from ctx.work(template,s.frame,slot['empty_template'],slot['roi'],.95)))
        if clear!=2 or after_money<=before_money:
            return Result('failed','sale_effect_unconfirmed',dict(sold=sold))
        ctx.checkpoint['uncertain_transaction']=False
        sold.append(dict(name=item['name'],slot=slot['point'],money_before=before_money,money_after=after_money,
                         item_present_before=True,slot_empty_after=True,identity_frame=identified.sequence,effect_frame=s.sequence))
        ctx.record('sale_confirmed',task=ctx.task,item=sold[-1],frame=s.sequence)
    final=yield from read_money(ctx,spec,folder)
    return Result('completed' if sold else 'skipped','sale_confirmed' if sold else 'no_approved_junk',
                  dict(sold=sold,money_before=initial,money_after=final),(str(folder),))
