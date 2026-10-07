"""Unified CLI and task composition. Only this module constructs live devices."""
import argparse
from contextlib import ExitStack
from dataclasses import asdict
import json
import math
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from runtime_types import Result
from runtime_store import Store, atomic_json
from runtime_engine import Perception, Executor, Scheduler
from runtime_world import ROOT, Capabilities, Places, load_profile
from runtime_skills import verify_unengaged, backpack, combat, loot, roam, navigate, escape, fresh, location, disengage_unreachable, recover, maintain_self_buff, prepare


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task',choices=['patrol','hunt','combat','bag','loot','skin','gather','gather-station','roam','escape','recover','navigate','vendor','mining','fish','observe','safety','preflight'],default='patrol')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--class', dest='character_class', choices=['warlock','paladin'], default='warlock',
                   help='职业配置：warlock 术士 / paladin 圣骑士；旧命令默认术士')
    p.add_argument('--check-task',choices=['patrol','navigate','mining','gather-station','vendor','fish'],default='patrol',help='Task to inspect with --task preflight')
    p.add_argument('--resource',choices=['mining','herbalism'],help='Resource for --task gather-station')
    p.add_argument('--station-confirmed',action='store_true',help='Operator has placed the character at a verified stationary gathering position')
    p.add_argument('--trial',action='store_true',help='Bounded validation run; never marks acceptance passed')
    p.add_argument('--audit-actions',action='store_true',help='Save every local CV frame for before/after action review')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--rounds',type=int,default=0)
    p.add_argument('--kills','--count',type=int,default=0)
    p.add_argument('--combat-seconds',type=float,default=90)
    p.add_argument('--max-seconds',type=float,default=1800)
    p.add_argument('--no-loot',action='store_true')
    p.add_argument('--skinning',action=argparse.BooleanOptionalAction,default=False)
    p.add_argument('--mining',action=argparse.BooleanOptionalAction,default=False)
    p.add_argument('--reserve-slots',type=int,default=1)
    p.add_argument('--until-full',action='store_true')
    p.add_argument('--vendor',nargs=2,type=float,default=(30.1,71.6))
    p.add_argument('--hunt-point',nargs=2,type=float,default=(24.2,73.6))
    p.add_argument('--keep-cycles',type=int,default=100)
    p.add_argument('--profile',type=Path,help='Override the selected class vision profile')
    p.add_argument('--runtime-profile',type=Path,default=ROOT/'calibration/runtime.json')
    p.add_argument('--places',type=Path,default=ROOT/'calibration/places.json')
    p.add_argument('--map-id')
    p.add_argument('--route')
    p.add_argument('--target',nargs=2,type=float)
    p.add_argument('--destination',help='Remembered place name in the explicitly selected map')
    p.add_argument('--casts',type=int,default=30)
    p.add_argument('--audio',action='store_true')
    p.add_argument('--record-route',action='store_true')
    p.add_argument('--skip-unknown',action='store_true',help='Compatibility: unified combat always uses bounded unknown skipping')
    p.add_argument('--stop-after-xp',type=int,default=1)
    p.add_argument('--direction',choices=['left','right'],default='right')
    p.add_argument('--point',nargs=2,type=int)
    p.add_argument('--max-steps',type=int,default=120)
    return p


def validate(a):
    if a.rounds<0 or a.kills<0 or not 1<=a.combat_seconds<=300 or not 0<a.max_seconds<=86400 or a.stop_after_xp<0:
        raise ValueError('Invalid task limits')
    if not 1<=a.reserve_slots<=20 or a.keep_cycles<1 or a.casts<1 or not 1<=a.max_steps<=120:
        raise ValueError('Invalid capacity/retention/cast count')
    for point in (a.target,a.vendor,a.hunt_point):
        if point and any(not math.isfinite(v) or not 0<=v<=100 for v in point):
            raise ValueError('Coordinates must be finite and 0..100')
    if a.trial and a.max_seconds>300:
        raise ValueError('--trial requires --max-seconds <= 300')
    if a.task=='gather-station':
        if not a.resource or not a.station_confirmed or a.max_seconds>90:
            raise ValueError('gather-station requires --resource, --station-confirmed and --max-seconds <= 90')
    if a.task=='preflight' and a.check_task=='gather-station' and not a.resource:
        raise ValueError('stationary gathering preflight requires --resource')
    if a.destination and (a.target or a.task not in ('navigate','preflight')):
        raise ValueError('--destination is a navigation target and cannot be combined with --target')
    if a.task not in ('observe','safety','preflight') and not a.execute:
        raise ValueError('Physical skills require --execute; use --task observe or preflight for read-only checks')
    return a


def check_profile(spec, name):
    required={
        'core':[],
        'navigation':[],
        'mining':['names','receipt_items','cursor_template','node_templates'],
        'vendor':['name','window_template','window_roi','money_roi','item_tooltip_roi','allowed_junk','slots'],
        'fishing':['cast_key','world_roi','bobber_template','receipt_items']}
    for key in required[name]:
        if not spec.get(key):
            raise ValueError(f'{name}: missing {key}')
    assets=[]
    def roi(value):
        if (not isinstance(value,(list,tuple)) or len(value)!=4 or any(type(v) is not int for v in value)
                or value[0]<0 or value[1]<0 or value[2]<=0 or value[3]<=0
                or value[0]+value[2]>1920 or value[1]+value[3]>1080):
            raise ValueError(f'{name}: invalid calibrated ROI')
    def point(value):
        if (not isinstance(value,(list,tuple)) or len(value)!=2 or any(type(v) is not int for v in value)
                or not 375<=value[0]<=1645 or not 150<=value[1]<=940):
            raise ValueError(f'{name}: invalid calibrated pointer position')
    for key in ('names','receipt_items'):
        if key in required[name] and (not isinstance(spec[key],list) or any(not isinstance(v,str) or not v.strip() for v in spec[key])):
            raise ValueError(f'{name}: invalid {key}')
    if name in ('mining','fishing'):
        for key in ('skill_confirmed','tool_confirmed'):
            if spec.get(key) is not True:
                raise ValueError(f'{name}: {key} required for the current character')
    if name=='mining':
        assets=[spec['cursor_template']]+[v['file'] for v in spec['node_templates']]
        for item in spec['node_templates']:
            roi(item.get('roi'))
            threshold=item.get('threshold',.95)
            if type(threshold) not in (int,float) or not math.isfinite(threshold) or not .5<=threshold<=1:
                raise ValueError('mining: invalid template threshold')
    elif name=='fishing':
        assets=[spec['bobber_template']]
        roi(spec['world_roi'])
        if spec.get('visual_verified') is not None and type(spec['visual_verified']) is not bool:
            raise ValueError('fishing: visual_verified must be boolean')
        if spec.get('visual_verified') is True:
            if not spec.get('visual_bite_template'):
                raise ValueError('Verified visual fishing requires bite calibration')
            assets.append(spec['visual_bite_template'])
    elif name=='vendor':
        assets=[spec['window_template']]+[v['icon'] for v in spec['allowed_junk']]+[v['empty_template'] for v in spec['slots']]
        for key in ('window_roi','money_roi','item_tooltip_roi'):
            roi(spec[key])
        roi(spec.get('merchant_tooltip_roi',(1390,740,250,150)))
        for value in spec.get('merchant_points',[]):
            point(value)
        names=set()
        for item in spec['allowed_junk']:
            if (item.get('approved') is not True or item.get('category')!='junk' or item.get('quality')!='poor'
                    or not isinstance(item.get('name'),str) or not item['name'].strip() or item['name'] in names):
                raise ValueError('vendor: whitelist must contain distinct approved poor junk')
            names.add(item['name'])
    for asset in assets:
        if not (ROOT/asset).is_file():
            raise ValueError(f'{name}: missing calibration asset {asset}')
    if name=='fishing' and (type(spec['cast_key']) is not int or not 4<=spec['cast_key']<=231):
        raise ValueError('Invalid fishing key')
    if name=='vendor':
        for slot in spec['slots']:
            if not all(slot.get(k) for k in ('roi','point','empty_template')):
                raise ValueError('Every vendor slot needs ROI, pointer position and empty template')
            roi(slot['roi'])
            point(slot['point'])
            x,y,w,h=slot['roi']
            if not (x<=slot['point'][0]<x+w and y<=slot['point'][1]<y+h):
                raise ValueError('vendor: sale pointer must lie inside its identified slot')


def preflight(a, profile):
    if a.task=='preflight':
        import copy
        a=copy.copy(a)
        a.task=a.check_task
    registry=json.loads((ROOT/'calibration/capabilities.json').read_text())
    cap=Capabilities(profile,registry,a.trial,extra_paths=(a.profile,a.places))
    required=set()
    if a.task not in ('observe','preflight'):
        required.add('core')
    if a.task in ('navigate','vendor','mining'):
        required.add('navigation')
    if a.task=='vendor':
        required.add('vendor')
    if a.mining or a.task=='mining':
        required.add('mining')
        required.add('navigation')
    if a.task=='fish':
        required.add('fishing')
    if a.task=='gather-station':
        required.add('stationary_gather')
    errors=[]
    from threat_state import ThreatMonitor
    try:ThreatMonitor(**profile.get('threat',{}))
    except (TypeError,ValueError):errors.append('Invalid threat settings')
    from class_profiles import load_class, policy_options
    character = load_class(getattr(a, 'character_class', 'warlock'))
    if a.task not in ('observe','gather-station'):
        try:
            policy_options(character)
        except ValueError as exc:
            errors.append(str(exc))
    for name in sorted(required):
        try:
            spec=cap.require(name)
            if name=='stationary_gather':
                from stationary_gather import validate_stationary_profile, validate_general_chat
                validate_stationary_profile(spec,a.resource,a.character_class)
                vision=json.loads(a.profile.read_text()) if a.profile else json.loads((ROOT/character['vision_profile']).read_text())
                validate_general_chat(vision)
            else:
                check_profile(spec,name)
        except Exception as exc:
            errors.append(str(exc))
    if a.task=='gather-station':
        from run_permission import input_effect_issues
        errors.extend(input_effect_issues(ROOT,a.character_class))
    if required & {'navigation','mining','vendor'}:
        if not a.map_id or a.map_id=='unconfirmed' or profile['map_id']!=a.map_id:
            errors.append('An explicitly confirmed map ID matching the calibration is required')
        route_name=a.route or (profile.get('vendor',{}).get('outbound_route') if a.task=='vendor' else None)
        target=a.target
        if a.destination:
            try:
                target=Places(a.places).place(a.destination,a.map_id)
            except Exception as exc:
                errors.append(str(exc))
        if not route_name and not (a.task=='navigate' and a.trial and target):
            errors.append('A verified --route is required; bounded navigation trials may use --target')
        if route_name:
            try:
                places=Places(a.places)
                outbound=places.route(route_name,a.map_id)
                if a.task=='navigate' and target and math.dist(outbound[-1],target)>.2:
                    raise ValueError('Verified route does not end at the requested destination')
                if a.task=='vendor':
                    inbound=places.route(profile['vendor']['return_route'],a.map_id)
                    if math.dist(outbound[-1],a.vendor)>.2 or math.dist(inbound[-1],a.hunt_point)>.2:
                        raise ValueError('Configured routes do not end at requested vendor/hunt coordinates')
            except Exception as exc:
                errors.append(str(exc))
    if a.audio or a.task=='fish':
        audio=profile['audio']
        from runtime_audio import validate_audio_config
        try:
            validate_audio_config(audio,require_detection=a.task=='fish' and profile['fishing'].get('visual_verified') is not True)
        except (ValueError,TypeError,KeyError) as exc:
            errors.append(str(exc))
        if not audio.get('device_uid') or not (ROOT/'audio_capture').exists():
            errors.append('Configure USB Audio UID and compile audio_capture.swift')
        if a.task=='fish' and (audio.get('offset_seconds') is None or 'bite' not in audio.get('templates',{})):
            if profile['fishing'].get('visual_verified') is not True:
                errors.append('Fishing requires measured audio offset and a calibrated bite template')
        for name,spec in audio.get('templates',{}).items():
            if not (ROOT/spec['file']).is_file():
                errors.append('Missing audio template: '+name)
    from run_permission import permission_issues
    errors.extend(permission_issues(ROOT,a.character_class,a.check_task if a.task=='preflight' else a.task,
                                  dict(kills=a.kills,max_seconds=a.max_seconds),
                                  required=character.get('run_permission_required',False)))
    return dict(ready=not errors,issues=errors,capabilities=registry,checked_task=a.task,
                character_class=character['class'],
                readiness_scope='requested_task_only',
                hardware_opened=False,trial=a.trial)


class Controller:
    def __init__(self,args,scheduler,store,profile,audio=None):
        self.a,self.s,self.store,self.profile,self.audio=args,scheduler,store,profile,audio
        self.deadline=time.monotonic()+args.max_seconds
        self.cycles=self.kills=0
        self.looting=not args.no_loot
        self.workflow=getattr(scheduler,'workflow_options',{})
        from workflow_state import InventoryCache
        self.inventory=InventoryCache(self.workflow.get('inventory_cache_seconds',30))
        self.visited_minerals=set()
        self.visited_mining_positions=[]

    def ensure_safe_exit(self,result):
        if (result.status!='failed' and result.reason!='run_deadline') or self.a.task not in ('patrol','hunt','combat'):
            return result
        if 'safety_exit' in result.facts:return result
        from runtime_safety import resolve_threat
        safety=resolve_threat(self.s,self.s.clock()+55)
        self.store.emit('failure_exit_verified',decision=safety.reason,original_reason=result.reason)
        return Result(result.status,result.reason,dict(result.facts,**safety.facts),result.evidence+safety.evidence)

    def inventory_identity(self):
        source=getattr(self.s,'source',None)
        snapshot=source.peek() if source else None
        return (self.a.character_class,str(self.a.profile),
                (getattr(snapshot,'calibration',None),getattr(snapshot,'calibration_generation',None)) if snapshot else None)

    def inventory_skill(self,name,factory,seconds=90,**kwargs):
        self.inventory.invalidate()  # Also invalidate on partial/uncertain transactions.
        return self.run_skill(name,factory,seconds,**kwargs)

    def run_skill(self,name,factory,seconds=90,interruptible=True,resume=True):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:
            return Result('cancelled','run_deadline')
        self.store.status(state=name,cycle=self.cycles,confirmed_kills=self.kills,loot_enabled=self.looting)
        defense_kills=getattr(self.s,'defense_kills',0)
        result=self.s.run(name,factory,min(seconds,remaining),interruptible,resume,deadline=self.deadline)
        self.kills+=getattr(self.s,'defense_kills',0)-defense_kills
        return result

    def navigation(self,points,allow_mining=False):
        def detour(ctx,position):
            if not allow_mining or not self.a.mining or not self.looting:
                return None
            eligible=[p for p in self.s.source.minerals if p['identity'] not in self.visited_minerals and 0<=ctx.clock()-p['at']<=1.2]
            if not eligible:
                return None
            if any(math.dist(position,p)<.3 for p in self.visited_mining_positions):
                return None
            candidate=min(eligible,key=lambda p:math.hypot(p['x']-1535,p['y']-280))
            self.visited_minerals.add(candidate['identity'])
            self.visited_mining_positions.append(position)
            bag=yield from backpack(ctx)
            if bag.status!='completed' or bag.facts['empty']<1:
                return Result('skipped','no_confirmed_capacity_for_mining')
            from runtime_interactions import mining
            self.inventory.invalidate()
            deadline=ctx.deadline
            ctx.deadline=min(deadline,ctx.clock()+90)
            try:
                return (yield from mining(ctx,self.s,self.profile['mining'],candidate,self.store.folder,position))
            finally:
                ctx.deadline=deadline
        return self.run_skill('navigation',lambda c:navigate(c,self.store.folder,points,detour,max_steps=self.a.max_steps),240)

    def merchant_trip(self):
        self.inventory.invalidate()
        # Full trips require validated outbound and return routes. No fabricated
        # straight line to a remembered coordinate is promoted to a known route.
        spec=self.profile.get('vendor',{})
        registry=json.loads((ROOT/'calibration/capabilities.json').read_text())
        try:
            capabilities=Capabilities(self.profile,registry,self.a.trial,extra_paths=(self.a.profile,self.a.places))
            check_profile(capabilities.require('vendor'),'vendor')
            check_profile(capabilities.require('navigation'),'navigation')
            if not self.a.map_id or self.a.map_id=='unconfirmed' or self.profile.get('map_id')!=self.a.map_id:
                raise ValueError('Merchant map is not confirmed')
            places=Places(self.a.places)
            outbound=places.route(self.a.route or spec['outbound_route'],self.a.map_id)
            inbound=places.route(spec['return_route'],self.a.map_id)
            if math.dist(outbound[-1],self.a.vendor)>.2 or math.dist(inbound[-1],self.a.hunt_point)>.2:
                raise ValueError('Configured routes do not end at requested vendor/hunt coordinates')
        except Exception as exc:
            self.looting=False
            return Result('skipped','vendor_unavailable_kill_only',dict(reason=str(exc)))
        arrived=self.navigation(outbound)
        if arrived.status!='completed':
            return Result('failed','vendor_route_failed',dict(reason=arrived.reason))
        from runtime_interactions import sell_junk,close_merchant
        sale=self.run_skill('vendor',lambda c:sell_junk(c,spec,self.store.folder/c.task),90,resume=False)
        if sale.status=='cancelled':
            return sale
        closed=self.run_skill('vendor_close',lambda c:close_merchant(c,spec),15,resume=False)
        if closed.status!='completed':
            return Result(closed.status,'vendor_return_unsafe_dialog',dict(reason=closed.reason,sale=asdict(sale)))
        bag=self.run_skill('backpack',backpack,45,resume=False)
        if bag.status=='cancelled':
            return bag
        returned=self.navigation(inbound)
        if returned.status!='completed':
            return Result('failed','vendor_return_failed',dict(reason=returned.reason))
        if sale.status!='completed' or bag.status!='completed' or bag.facts.get('empty',0)<1:
            self.looting=False
            return Result('skipped','vendor_failed_kill_only',dict(sale=asdict(sale)))
        return Result('completed','vendor_trip_complete',dict(sale=asdict(sale),empty=bag.facts['empty']),
                      tuple(sale.evidence)+tuple(bag.evidence)+tuple(returned.evidence))

    def patrol(self):
        a=self.a
        precombat_retries=0
        while time.monotonic()<self.deadline:
            if a.rounds and self.cycles>=a.rounds:
                return Result('completed','ROUND_LIMIT_COMPLETE',dict(cycles=self.cycles,confirmed_kills=self.kills))
            self.cycles+=1
            ready=self.run_skill('ready',lambda c:prepare(c,self.s),75,False,False)
            if ready.status!='completed':return ready
            if a.kills and self.kills>=a.kills:
                return Result('completed','KILL_LIMIT_COMPLETE',dict(confirmed_kills=self.kills,cycles=self.cycles))
            if self.looting:
                identity=self.inventory_identity()
                bag=self.inventory.get(time.monotonic(),identity)
                if bag is None:
                    bag=self.run_skill('backpack',backpack,45,resume=False)
                    self.inventory.remember(bag,time.monotonic(),identity)
                if bag.status!='completed':
                    return bag
                if bag.facts['empty']<a.reserve_slots:
                    if bag.facts['empty']==0:
                        if a.until_full or a.task=='hunt':
                            return Result('completed','BAG_FULL' if a.until_full else 'NEED_VENDOR',dict(empty_slots=0))
                        vendor=self.merchant_trip()
                        if vendor.status in ('failed','cancelled'):
                            return vendor
                        continue
                    return Result('completed','NEED_VENDOR',dict(empty_slots=bag.facts['empty']))
            hunt=self.run_skill('combat',lambda c:combat(c,self.s,a.combat_seconds),a.combat_seconds+46,False)
            # A later failed escape cannot erase already observed XP evidence.
            self.kills+=hunt.facts.get('xp_events',0)
            if hunt.status=='completed':
                precombat_retries=0
                if self.looting:
                    recovery=self.run_skill('recover_before_loot',lambda c:recover(c,health_only=True),60,False,False)
                    if recovery.status!='completed':
                        if recovery.status=='cancelled':return recovery
                        return Result(recovery.status,'recovery_before_loot_failed',
                                      dict(recovery.facts,recovery_result=asdict(recovery),confirmed_kills=self.kills),recovery.evidence)
                    picked=self.inventory_skill('loot',lambda c:loot(c,self.store.folder),75,resume=False)
                    if picked.status=='cancelled':
                        return picked
                    # Loot confirmation belongs to the shared workflow, not a class.
                    if picked.status!='completed':
                        return Result('failed','loot_unconfirmed',dict(picked.facts,loot_result=asdict(picked),confirmed_kills=self.kills),picked.evidence)
                    if picked.status=='completed' and a.skinning:
                        skin=self.inventory_skill('skin',lambda c:loot(c,self.store.folder,'skin'),20,resume=False)
                        if skin.status in ('failed','cancelled'):
                            return skin
                if a.kills and self.kills>=a.kills:
                    return Result('completed','KILL_LIMIT_COMPLETE',dict(confirmed_kills=self.kills,cycles=self.cycles))
            elif hunt.reason in ('opener_target_not_located','opener_approach_limit','opener_not_confirmed') and a.task=='patrol':
                if precombat_retries>=3:
                    return Result('failed',hunt.reason,dict(confirmed_kills=self.kills))
                checked=self.run_skill('verify_unengaged',verify_unengaged,5,False,False)
                if checked.status!='completed':
                    return Result(checked.status,checked.reason,dict(checked.facts,confirmed_kills=self.kills),checked.evidence)
                precombat_retries+=1
            elif hunt.reason=='click_to_move_no_damage' and a.task=='patrol':
                cleared=self.run_skill('disengage',disengage_unreachable,8,False,False)
                if cleared.status!='completed':
                    if cleared.status=='cancelled':return cleared
                    return Result('failed',hunt.reason,dict(cleared.facts,confirmed_kills=self.kills,combat_result=asdict(hunt)),cleared.evidence)
            elif hunt.reason not in ('no_targets_found','duration_limit_out_of_combat','target_not_allowed'):
                from runtime_safety import needs_review
                return Result(hunt.status,hunt.reason,dict(hunt.facts,confirmed_kills=self.kills,
                              combat_result=asdict(hunt),review_required=needs_review(hunt.reason)),hunt.evidence)
            if a.task!='hunt' and (hunt.status!='completed' or self.workflow.get('roam_after_kill',True)):
                moved=self.run_skill('roam',lambda c:roam(c,'left' if self.cycles%2==0 else 'right'),125)
                if moved.status!='completed':
                    return moved
            if a.mining and self.looting:
                origin=self.run_skill('position',lambda c:self.position_result(c),20)
                if origin.status!='completed':
                    return origin
                mined=self.navigation([origin.facts['position']],allow_mining=True)
                if mined.status in ('failed','cancelled'):
                    return mined
        return Result('cancelled','run_deadline',dict(confirmed_kills=self.kills,cycles=self.cycles))

    def position_result(self,ctx):
        p=yield from location(ctx,self.store.folder)
        return Result('completed','position_confirmed',dict(position=p),tuple(ctx.checkpoint.get('position_evidence',())))

    def run(self):
        a=self.a
        if a.task in ('patrol','hunt'):
            return self.patrol()
        if a.task=='recover':
            return self.run_skill('recover',recover,a.max_seconds,False,False)
        if a.task=='gather':
            facts={}
            results=[]
            if a.skinning:
                result=self.inventory_skill('skin',lambda c:loot(c,self.store.folder,'skin'),20,resume=False)
                facts['skinning']=asdict(result)
                results.append(result)
                if result.status in ('failed','cancelled'):
                    return Result(result.status,'optional_gather_'+result.status,facts,result.evidence)
            if a.mining:
                original=a.task
                a.task='mining'
                try:
                    result=self.run()
                    facts['mining']=asdict(result)
                    results.append(result)
                finally:a.task=original
            status=next((r.status for r in results if r.status in ('failed','cancelled')),None)
            status=status or ('completed' if any(r.status=='completed' for r in results) else 'skipped')
            return Result(status,'optional_gather_finished' if status in ('completed','skipped') else 'optional_gather_'+status,
                          facts,tuple(e for r in results for e in r.evidence))
        if a.task=='observe':
            def observe(ctx):
                count=0
                while ctx.clock()<ctx.deadline-.15:
                    yield from ctx.observe()
                    count+=1
                return Result('completed','observation_finished',dict(frames=count,input_sent=False))
            return self.run_skill('observe',observe,a.max_seconds,False)
        if a.task=='safety':
            from runtime_safety import resolve_threat
            return resolve_threat(self.s,min(self.deadline,self.s.clock()+a.max_seconds))
        factories={
            'combat':lambda c:combat(c,self.s,a.combat_seconds,a.stop_after_xp),
            'bag':backpack,
            'loot':lambda c:loot(c,self.store.folder,point=a.point),
            'skin':lambda c:loot(c,self.store.folder,'skin',point=a.point),
            'roam':lambda c:roam(c,a.direction), 'escape':escape}
        if a.task in factories:
            return self.run_skill(a.task,factories[a.task],a.max_seconds,a.task not in ('combat','escape'),a.task not in ('loot','skin','bag'))
        if a.task=='navigate':
            places=Places(a.places)
            target=places.place(a.destination,a.map_id) if a.destination else a.target
            points=places.route(a.route,a.map_id) if a.route else [target]
            return self.navigation(points,True)
        if a.task=='vendor':
            return self.merchant_trip()
        if a.task=='mining':
            # Let the monitor gather three independent observations first.
            def gather(ctx):
                yield from ctx.pause(1.6)
                bag=yield from backpack(ctx)
                if bag.status!='completed' or bag.facts['empty']<1:
                    return Result('skipped','no_confirmed_capacity')
                if not self.s.source.minerals:
                    return Result('skipped','no_mineral_candidate')
                from runtime_interactions import mining
                return (yield from mining(ctx,self.s,self.profile['mining'],self.s.source.minerals[0],self.store.folder))
            return self.run_skill('mining',gather,90,resume=False)
        if a.task=='gather-station':
            bag=self.run_skill('backpack',backpack,45,False,False)
            if bag.status!='completed' or bag.facts.get('empty',0)<self.profile['stationary_gather']['settings']['minimum_empty_slots']:
                return Result('failed','gather_capacity_unconfirmed',dict(bag=asdict(bag)))
            from stationary_gather import gather_once
            # No automatic threat escape or resumption at an unverified terrain edge.
            return self.run_skill('gather_station',lambda c:gather_once(c,self.profile['stationary_gather'],a.resource,self.store.folder),
                                  min(45,a.max_seconds),False,False)
        if a.task=='fish':
            from runtime_audio import fishing
            return self.run_skill('fishing',lambda c:fishing(c,self.profile['fishing'],self.audio,self.store.folder/c.task,a.casts),a.max_seconds)
        raise ValueError('Unsupported task')


def main(argv=None):
    p=parser()
    a=p.parse_args(argv)
    try:
        validate(a)
        profile=load_profile(a.runtime_profile)
        from class_profiles import load_class, policy_options
        character=load_class(a.character_class)
        if a.profile is None:
            a.profile=ROOT/character['vision_profile']
    except (ValueError,OSError) as exc:
        p.error(str(exc))
    store=Store(a.output,vars(a),a.keep_cycles)
    result=Result('failed','startup_incomplete')
    try:
        atomic_json(a.output/'runtime-profile.json',profile)
        atomic_json(a.output/'class-profile.json',character)
        atomic_json(a.output/'vision-profile.json',json.loads(a.profile.read_text()))
        atomic_json(a.output/'places.json',json.loads(a.places.read_text()))
        checks=preflight(a,profile)
        atomic_json(a.output/'preflight.json',checks)
        if a.task=='preflight':
            result=Result('completed','preflight_complete',checks)
        elif not checks['ready']:
            result=Result('failed','preflight_failed',checks)
        else:
            from control_lock import controller_lock
            from vision_feed import Feed
            from vision_state import Vision
            from kmbox_tap import KMBox
            with ExitStack() as stack:
                stack.enter_context(controller_lock())
                import cv2
                cv2.setNumThreads(1)  # Keep recognition from starving live frame capture.
                vision=Vision(a.profile)
                route=None
                if a.record_route:
                    from route_recorder import RouteRecorder
                    route=RouteRecorder(a.output/'route',vision.profile['coordinate_roi'])
                    stack.callback(route.close)
                if profile.get('safety',{}).get('death_review_required',True):
                    from death_review import require_review
                    store.death_review_callback=lambda reason:require_review(ROOT,a.character_class,a.output,reason)
                source=Perception(Feed(),vision,store,monitor_minerals=a.mining or a.task=='mining',route=route)
                stack.callback(source.close)
                executor=Executor(KMBox() if a.execute and a.task not in ('observe','safety') else None,source,store.emit)
                executor.ctm_enabled=character['policy'].get('interact_key') is not None
                stack.callback(executor.close)
                scheduler=Scheduler(source,executor,store)
                from threat_state import ThreatMonitor
                scheduler.threat_monitor=ThreatMonitor(**profile.get('threat',{}))
                from run_permission import permission_issues
                scheduler.combat_permission=lambda task:permission_issues(
                    ROOT,a.character_class,
                    (a.task if a.task in ('combat','patrol','hunt','roam') else task)
                    if task in ('combat','patrol','hunt','roam','defend') else task,
                    dict(kills=a.kills,max_seconds=a.max_seconds),
                    required=character.get('run_permission_required',False))
                scheduler.combat_options=policy_options(character) if a.task not in ('observe','gather-station') else {}
                scheduler.recovery_options=dict(character.get('recovery',{}),mana_target=scheduler.combat_options.get('mana_resume_at',.85)) if a.task not in ('observe','gather-station') else {}
                scheduler.workflow_options=character.get('workflow',{})
                scheduler.self_buff_options=character.get('self_buff',{}) if a.task!='observe' else {}
                scheduler.self_buff_state_path=ROOT/'runs'/'buff-timers'/f'{a.character_class}.json'
                scheduler.self_buff_gate_path=ROOT/'runs'/'death-review-gates'/f'{a.character_class}.json'

                stack.callback(scheduler.close)
                audio=None
                if a.audio or a.task=='fish':
                    from runtime_audio import AudioSource
                    audio=AudioSource(profile['audio'],store)
                    stack.callback(audio.close)
                    scheduler.audio=audio
                store.clip('session_start')
                previous={}
                for sig in (signal.SIGINT,signal.SIGTERM):
                    previous[sig]=signal.signal(sig,lambda *_:scheduler.stop())
                stack.callback(lambda:[signal.signal(sig,handler) for sig,handler in previous.items()])
                controller=Controller(a,scheduler,store,profile,audio)
                try:
                    result=controller.run()
                except Exception as exc:
                    result=Result('failed',str(exc),dict(confirmed_kills=controller.kills))
                result=controller.ensure_safe_exit(result)
                if a.task in ('patrol','hunt'):
                    result=Result(result.status,result.reason,
                                  dict(result.facts,confirmed_kills=controller.kills),result.evidence)
    except KeyboardInterrupt:
        result=Result('cancelled','interrupted')
    except Exception as exc:
        result=Result('failed',str(exc))
    finally:
        try:
            store.close()
            from death_review import write_review_draft
            write_review_draft(ROOT,a.character_class,a.output)
        except Exception as exc:
            result=Result('failed','recording_close_failed',dict(original_result=asdict(result),error=str(exc)))
        if (store.error or store.worker.is_alive()) and result.reason!='recording_close_failed':
            result=Result('failed','evidence_recording_incomplete',dict(original_result=asdict(result),error=store.error))
        # Attempt each terminal file independently; one I/O error must not hide
        # the remaining terminal state or the process's nonzero exit status.
        finalization_errors=[]
        for write in (lambda:store.result(result),
                      lambda:store.status(state=result.reason,status=result.status,**result.facts)):
            try:
                write()
            except Exception as exc:
                finalization_errors.append(str(exc))
        if finalization_errors:
            result=Result('failed','terminal_state_write_failed',dict(original_result=asdict(result),errors=finalization_errors))
            for write in (lambda:store.result(result),
                          lambda:store.status(state=result.reason,status=result.status,**result.facts)):
                try:
                    write()
                except Exception:
                    pass  # Best effort only when the output filesystem is failing.
            print('Could not persist complete terminal state: '+'; '.join(finalization_errors),file=sys.stderr)
    print(json.dumps(asdict(result),ensure_ascii=False,default=str))
    return 0 if result.status=='completed' else 2


def legacy_entry(script, argv=None):
    """Translate old command-line spellings; all execution uses this runtime."""
    args=list(sys.argv[1:] if argv is None else argv)
    mapping={'run_controller.py':'combat','bag_probe.py':'bag','loot_probe.py':'loot',
             'navigate_local.py':'navigate','patrol_roam.py':'roam','combat_escape.py':'escape',
             'vendor_probe.py':'vendor','mineral_approach.py':'mining'}
    task=mapping.get(script,{'hunt_loot.py':'hunt','optional_gather.py':'gather'}.get(script,'patrol'))
    if script=='loot_probe.py' and '--skin' in args:
        args.remove('--skin')
        task='skin'
    if script=='run_controller.py':
        if '--stop-after-xp' not in args:
            args+=['--stop-after-xp','0']
        if '--execute' not in args:
            task='observe'
        if '--log' in args:
            index=args.index('--log')
            path=Path(args[index+1])
            args[index:index+2]=['--output',str(path.parent/(path.stem+'-runtime'))]
        if '--seconds' in args:
            index=args.index('--seconds')
            seconds=args[index+1]
            args[index:index+2]=['--combat-seconds',seconds,'--max-seconds',str(float(seconds)+46 if task=='combat' else float(seconds))]
    if script=='hunt_loot.py':
        if '--cycles' not in args and '--until-full' not in args:
            args+=['--rounds','1']
        if '--reserve-slots' not in args:
            args+=['--reserve-slots','1' if '--until-full' in args else '2']
        if '--cycles' in args:
            args[args.index('--cycles')]='--rounds'
        if '--until-full' in args:
            args+=['--rounds','0']
        if '--skin' in args:
            args[args.index('--skin')]='--skinning'
        if '--no-skin' in args:
            args[args.index('--no-skin')]='--no-skinning'
    if '--x' in args and '--y' in args:
        point=[]
        for key in ('--x','--y'):
            index=args.index(key)
            point.append(args[index+1])
            del args[index:index+2]
        args+=['--point' if task in ('loot','skin') else '--target',*point]
    return main(['--task',task,*args])


if __name__=='__main__':
    raise SystemExit(main())
