"""One resource at a human-confirmed stationary position; never steer or retry."""
import math
from pathlib import Path

import cv2

from interaction_vision import LootVision
from runtime_interactions import crop, ocr, read_receipts, receipt_confirmation
from runtime_skills import fresh, move_pointer, select_chat
from runtime_types import Result
from runtime_world import ROOT


def validate_stationary_profile(spec, resource, character):
    """Fail closed until one resource and its stationary interaction are calibrated."""
    if spec.get('stationary_interaction_verified') is not True:
        raise ValueError('stationary_gather: stationary interaction and click-to-move setting need live verification')
    settings=spec.get('settings',{})
    numeric={
        'minimum_health':(.9,1),
        'candidate_stability_px':(1,12),
        'candidate_separation_px':(8,40),
        'tooltip_confidence':(.7,1),
        'receipt_confidence':(.7,1),
        'receipt_wait_seconds':(6,20),
    }
    for key,(low,high) in numeric.items():
        value=settings.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError(f'stationary_gather: invalid {key}')
    for key,low,high in [('candidate_observations',2,4),('minimum_empty_slots',1,4)]:
        value=settings.get(key)
        if type(value) is not int or not low<=value<=high:
            raise ValueError(f'stationary_gather: invalid {key}')
    item=spec.get('resources',{}).get(resource,{})
    if item.get('calibrated') is not True:
        raise ValueError(f'stationary_gather: {resource} calibration_required')
    expected_kind={'mining':'mine','herbalism':'herb'}[resource]
    if item.get('cursor_kind')!=expected_kind:
        raise ValueError(f'stationary_gather: {resource} cursor_kind must be {expected_kind}')
    if type(item.get('requires_tool')) is not bool:
        raise ValueError(f'stationary_gather: {resource} requires_tool must be boolean')
    if resource=='mining' and item['requires_tool'] is not True:
        raise ValueError('stationary_gather: mining requires a confirmed tool')
    qualifications=spec.get('characters',{}).get(character,{}).get(resource,{})
    if qualifications.get('skill_confirmed') is not True:
        raise ValueError(f'stationary_gather: {character} {resource} skill_confirmed required')
    if item['requires_tool'] and qualifications.get('tool_confirmed') is not True:
        raise ValueError(f'stationary_gather: {character} {resource} tool_confirmed required')
    for key in ('names','receipt_items'):
        value=item.get(key)
        if not isinstance(value,list) or not value or any(not isinstance(v,str) or not v.strip() for v in value):
            raise ValueError(f'stationary_gather: {resource} missing {key}')
    for key in ('world_roi','tooltip_roi','receipt_roi'):
        roi=item.get(key)
        if (not isinstance(roi,list) or len(roi)!=4 or any(type(v) is not int for v in roi)
                or roi[2]<1 or roi[3]<1 or roi[0]<0 or roi[1]<0
                or roi[0]+roi[2]>1920 or roi[1]+roi[3]>1080):
            raise ValueError(f'stationary_gather: {resource} invalid {key}')
    x,y,w,h=item['world_roi']
    if x<375 or y<150 or x+w>1625 or y+h>885:
        raise ValueError('stationary_gather: world_roi outside calibrated pointer bounds')
    path=item.get('cursor_template')
    if not isinstance(path,str) or not (ROOT/path).is_file():
        raise ValueError(f'stationary_gather: {resource} cursor_template missing')
    cursor=cv2.imread(str(ROOT/path))
    if cursor is None or cursor.std()<2 or cursor.shape[0]>850 or cursor.shape[1]>1250:
        raise ValueError(f'stationary_gather: {resource} invalid cursor_template image')
    templates=item.get('node_templates')
    if not isinstance(templates,list) or not templates:
        raise ValueError(f'stationary_gather: {resource} node_templates missing')
    for entry in templates:
        if not isinstance(entry,dict) or not isinstance(entry.get('file'),str) or not (ROOT/entry['file']).is_file():
            raise ValueError(f'stationary_gather: {resource} node template missing')
        threshold=entry.get('threshold',.95)
        if type(threshold) not in (int,float) or not math.isfinite(threshold) or not .5<=threshold<=1:
            raise ValueError(f'stationary_gather: {resource} invalid template threshold')
        image=cv2.imread(str(ROOT/entry['file']))
        if image is None or image.std()<2 or image.shape[1]>w or image.shape[0]>h:
            raise ValueError(f'stationary_gather: {resource} invalid node template image')
    return item


def validate_general_chat(spec):
    """The first version needs one visible, calibrated native general tab."""
    tab=spec.get('chat_tabs',{}).get('general',{})
    roi=tab.get('roi')
    path=tab.get('file')
    if (tab.get('persistent') is not True or not isinstance(roi,list) or len(roi)!=4
            or any(type(v) is not int for v in roi) or roi[0]<0 or roi[1]<0
            or roi[2]<1 or roi[3]<1 or roi[0]+roi[2]>1920 or roi[1]+roi[3]>1080
            or not isinstance(path,str) or not (ROOT/path).is_file()):
        raise ValueError('stationary_gather: calibrated persistent general chat panel required')
    image=cv2.imread(str(ROOT/path))
    if image is None or image.shape[:2]!=(roi[3],roi[2]) or image.std()<2:
        raise ValueError('stationary_gather: invalid general chat template')


def world_candidates(frame, item, separation_px=16):
    """All separated template peaks, so a second node makes the choice ambiguous."""
    roi=item['world_roi']
    patch=crop(frame,roi)
    points=[]
    for entry in item['node_templates']:
        image=cv2.imread(str(ROOT/entry['file']))
        scores=cv2.matchTemplate(patch,image,cv2.TM_CCOEFF_NORMED)
        for _ in range(3):
            _,score,_,(x,y)=cv2.minMaxLoc(scores)
            if not math.isfinite(score) or score<entry.get('threshold',.95):
                break
            point=(roi[0]+x+image.shape[1]/2,roi[1]+y+image.shape[0]/2)
            if all(math.dist(point,old)>separation_px for old in points):
                points.append(point)
            radius=round(separation_px)
            scores[max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1]=-1
    return points


def confirmed_tooltip(data, names, confidence=.8):
    allowed={''.join(name.split()) for name in names}
    return any(''.join(row.get('text','').split()) in allowed and row.get('confidence',0)>=confidence
               for row in data.get('items',[]))


def gather_once(ctx, spec, resource, folder):
    """At most one world click. Any uncertainty ends this attempt without movement."""
    item=spec['resources'][resource]
    settings=spec['settings']
    minimum_health=settings['minimum_health']
    folder=Path(folder)/ctx.task
    ctx.phase='stationary_gather'
    tab=yield from select_chat(ctx,'general')
    if tab.status!='completed':
        return Result('failed','gather_chat_not_confirmed')
    point=None
    for observation in range(settings['candidate_observations']):
        snapshot=yield from fresh(ctx,minimum_health,True,True)
        candidates=yield from ctx.work(world_candidates,snapshot.frame,item,settings['candidate_separation_px'])
        if len(candidates)!=1:
            return Result('skipped','gather_world_node_ambiguous' if candidates else 'gather_world_node_absent',
                          dict(candidates=len(candidates),observation=observation+1))
        if point is not None and math.dist(point,candidates[0])>settings['candidate_stability_px']:
            return Result('skipped','gather_world_node_unstable')
        point=candidates[0]
    kind=item['cursor_kind']
    vision=LootVision({kind:item['cursor_template']})
    found=yield from move_pointer(ctx,vision,*point,expected=kind)
    if not found:
        return Result('skipped','gather_cursor_unconfirmed')
    for _ in range(2):
        snapshot=yield from fresh(ctx,minimum_health,True,True)
        data=yield from ctx.work(ocr,snapshot.frame,item['tooltip_roi'],folder,f'tooltip-{snapshot.sequence}')
        if not confirmed_tooltip(data,item['names'],settings['tooltip_confidence']):
            return Result('skipped','gather_name_unconfirmed')
    before=yield from read_receipts(ctx,folder,item['receipt_items'],'gather-before',
                                    item['receipt_roi'],settings['receipt_confidence'])
    baseline_check=yield from read_receipts(ctx,folder,item['receipt_items'],'gather-baseline-check',
                                            item['receipt_roi'],settings['receipt_confidence'])
    if baseline_check!=before:
        return Result('skipped','gather_receipt_baseline_unstable')
    found=yield from move_pointer(ctx,vision,*point,expected=kind)
    if not found:
        return Result('skipped','gather_cursor_changed')
    snapshot,_=found
    if not snapshot.casting_known or snapshot.observation.casting:
        return Result('skipped','gather_casting_unknown_or_active')
    if snapshot.observation.target:
        return Result('skipped','gather_target_not_clear')
    last=yield from ctx.work(ocr,snapshot.frame,item['tooltip_roi'],folder,f'tooltip-final-{snapshot.sequence}')
    if not confirmed_tooltip(last,item['names'],settings['tooltip_confidence']):
        return Result('skipped','gather_name_changed_before_click')
    found=yield from move_pointer(ctx,vision,*point,expected=kind)
    if not found:
        return Result('skipped','gather_cursor_changed_before_click')
    snapshot,_=found
    if not snapshot.casting_known or snapshot.observation.casting or snapshot.observation.target:
        return Result('skipped','gather_state_changed_before_click')
    ctx.checkpoint['uncertain_transaction']=True
    yield from ctx.act(snapshot,'click',(1,),f'gather_{resource}',minimum_health,True)
    if (yield from receipt_confirmation(ctx,folder,item['receipt_items'],before,
                                        settings['receipt_wait_seconds'],item['receipt_roi'],
                                        settings['receipt_confidence'])):
        ctx.checkpoint['uncertain_transaction']=False
        return Result('completed','gather_personal_receipt_confirmed',
                      dict(resource=resource,clicks=1,confirmed=1),(str(folder),))
    return Result('failed','gather_receipt_unconfirmed',dict(resource=resource,clicks=1,confirmed=0),
                  (str(folder),))
