"""Cooperative existing skills. No hardware, subprocess orchestration, or sleeps."""
import math
from pathlib import Path
import time
from interaction_vision import BagVision
from interaction_vision import LootVision
from control_policy import Policy
from navigation_coordinate import coordinate
from navigate_local import steering, oscillating
from coin_receipt import read_coin_receipts, confirmed_pair
from skin_receipt import read_skin_receipts, increased
from runtime_types import Result, GuardFailed

ROOT = Path(__file__).resolve().parent


def fresh(ctx, minimum_hp=0, peaceful=False, no_target=False):
    s = yield from ctx.observe()
    o = s.observation
    if not o.valid or o.player_hp < minimum_hp:
        raise GuardFailed('invalid_or_low_health')
    if peaceful and (not s.combat_known or o.in_combat):
        raise GuardFailed('combat_or_unknown')
    if no_target and o.target:
        raise GuardFailed('target_must_be_clear')
    return s


def tap(ctx, key, ms, reason, minimum_hp=0, peaceful=False):
    s = yield from fresh(ctx, minimum_hp, peaceful)
    yield from ctx.act(s, 'tap', (key,ms), reason, minimum_hp, peaceful)
    yield from ctx.pause(ms/1000+.12)


def move_pointer(ctx, vision, x, y, bounds=(375,150,1625,885), expected=None, tolerance=8):
    if not bounds[0]<=x<=bounds[2] or not bounds[1]<=y<=bounds[3]:
        raise GuardFailed('pointer_destination_outside_calibration')
    for _ in range(24):
        s = yield from fresh(ctx, .8, True)
        cursor = yield from ctx.work(vision.cursor, s.frame)
        if cursor is None:
            raise GuardFailed('cursor_not_located')
        if ctx.clock()-s.captured_at>.45:
            continue
        dx,dy = x-cursor['x'], y-cursor['y']
        if abs(dx)<=tolerance and abs(dy)<=tolerance:
            if expected and cursor['kind']!=expected:
                return None
            return s,cursor
        step = tuple(max(-60,min(60,round(v/2))) for v in (dx,dy))
        yield from ctx.act(s,'move',step,'position_pointer',.8,True)
        yield from ctx.pause(.12)
    raise GuardFailed('pointer_convergence_limit')


def combat(ctx, scheduler, seconds=90, xp_limit=1):
    policy = Policy(skip_unknown=True)
    started = ctx.clock()
    last_engaged = started
    target_track=attack_track=None
    while True:
        s = yield from fresh(ctx)
        if not s.combat_known:
            raise GuardFailed('combat_status_unknown')
        o,now = s.observation,ctx.clock()
        if s.target_track!=target_track:
            # Preserve XP/rest counters, but never transfer damage or attack
            # history to another observable target continuity track.
            target_track=s.target_track
            attack_track=None
            policy.last_hp=None
            policy.damaged_target=False
            policy.last_attempt=None
            policy.fight_started=None
            policy.approaches=0
            policy.last_progress=now
        action = policy.step(o,now,now-s.captured_at)
        if o.in_combat or policy.damaged_target:
            last_engaged = now
        if policy.damaged_target and target_track is not None and attack_track==target_track:
            scheduler.note_engagement(s)
        if now-last_engaged>=30 and not o.in_combat and (policy.last_attempt is None or now-policy.last_attempt>4):
            policy.stop('no_targets_found')
        if o.in_combat and o.player_hp<.55:
            policy.stop('combat_health_retreat')
        if xp_limit and policy.xp_events>=xp_limit and not o.in_combat:
            policy.stop('xp_limit_out_of_combat')
        if now-started>=seconds:
            if not o.in_combat:
                policy.stop('duration_limit_out_of_combat')
            elif now-started>=seconds+45:
                policy.stop('duration_combat_grace_exhausted')
            if action and action.reason!='attack':
                action=None
        ctx.record('combat_state', task=ctx.task, state=policy.state, stop_reason=policy.stopped,
                   xp_events=policy.xp_events, frame=s.sequence)
        if policy.stopped:
            completed = policy.stopped=='xp_limit_out_of_combat'
            return Result('completed' if completed else 'failed', policy.stopped,
                          dict(xp_events=policy.xp_events, out_of_combat=not o.in_combat,
                               rest_cycles=policy.rest_cycles, identity_verified=False), (s.sequence,))
        if action:
            yield from ctx.act(s,'tap',(action.key,action.milliseconds),action.reason,.55 if o.in_combat else .4)
            if action.reason=='attack':
                attack_track=target_track


def escape(ctx):
    clear = 0
    for key,ms in [(79,500)]*2+[(26,500)]*24:
        s = yield from fresh(ctx)
        if not s.combat_known:
            return Result('failed','combat_unknown')
        if not s.observation.in_combat:
            clear+=1
            if clear>=3:
                return Result('completed','ESCAPED',dict(out_of_combat=True,hp=s.observation.player_hp),(s.sequence,))
            yield from ctx.pause(.2)
            continue
        clear=0
        yield from ctx.act(s,'tap',(key,ms),'escape')
        yield from ctx.pause(ms/1000+.12)
    return Result('failed','ESCAPE_LIMIT')


def recover(ctx):
    count=0
    while True:
        s=yield from fresh(ctx)
        o=s.observation
        if not s.combat_known or o.in_combat:
            return Result('failed','combat_restarted_during_recovery')
        count=count+1 if o.player_hp>=.95 and o.player_mana>=.85 else 0
        if count>=3:
            return Result('completed','recovered',dict(hp=o.player_hp,mana=o.player_mana),(s.sequence,))
        yield from ctx.pause(.2)


def backpack(ctx):
    bag, cursor = BagVision(), LootVision()
    ctx.phase='backpack'
    yield from move_pointer(ctx,cursor,800,250,expected='hand')
    s=yield from fresh(ctx,.8,True)
    initial=yield from ctx.work(bag.observe,s.frame)
    if not initial['open']:
        yield from tap(ctx,5,80,'open_backpack',.8,True)
    stable=0
    previous=None
    reading={}
    for _ in range(10):
        s=yield from fresh(ctx,.8,True)
        reading=yield from ctx.work(bag.observe,s.frame)
        ctx.record('backpack_reading',task=ctx.task,frame=s.sequence,reading=reading)
        signature=tuple(v['state'] for v in reading.get('slots',[]))
        stable=(stable+1 if signature==previous else 1) if reading.get('valid') else 0
        previous=signature
        if stable>=3:
            break
        yield from ctx.pause(.12)
    observed_at=s.sequence
    # Only toggle a window observed as open; never perform cleanup on cancellation.
    s=yield from fresh(ctx,.8,True)
    current=yield from ctx.work(bag.observe,s.frame)
    if current['open']:
        yield from tap(ctx,5,80,'close_backpack',.8,True)
    s=yield from fresh(ctx,.8,True)
    closed=not (yield from ctx.work(bag.observe,s.frame))['open']
    if stable<3 or not closed:
        return Result('failed','backpack_unconfirmed',dict(closed_verified=closed))
    return Result('completed','backpack_confirmed',dict(reading,closed_verified=True), (observed_at,s.sequence))


def loot(ctx, folder, interaction='loot', point=None):
    folder=Path(folder)/ctx.task
    folder.mkdir(parents=True,exist_ok=True)
    vision=LootVision()
    ctx.phase=interaction
    if point:
        points=[dict(x=point[0],y=point[1])]
    elif interaction=='skin':
        s=yield from fresh(ctx,.8,True)
        cursor=yield from ctx.work(vision.cursor,s.frame)
        if not cursor or cursor['kind']!='skin':
            return Result('skipped','no_skin_cursor')
        points=[cursor]
    else:
        s=yield from fresh(ctx,.8,True)
        points=yield from ctx.work(vision.candidates,s.frame)
        points+= [dict(x=x,y=y) for y in (530,620,710,800,850) for x in (1005,955,1055,895,1115)]
    for p in points:
        found=yield from move_pointer(ctx,vision,p['x'],p['y'],expected=interaction)
        if not found:
            continue
        s,cursor=found
        before=vision.loot_messages(s.frame)
        reader=read_skin_receipts if interaction=='skin' else read_coin_receipts
        baseline=yield from ctx.work(reader,s.frame,folder,'before')
        # Slow OCR never authorizes a click. Reacquire and recheck the cursor.
        found=yield from move_pointer(ctx,vision,p['x'],p['y'],expected=interaction)
        if not found:
            return Result('failed','cursor_changed_before_click')
        s,_=found
        ctx.checkpoint['uncertain_transaction']=True
        yield from ctx.act(s,'click',(1,),interaction,.8,True)
        messages=skinnable=hands=0
        for _ in range(25):
            yield from ctx.pause(.15)
            s=yield from fresh(ctx,.8,True)
            cursor=yield from ctx.work(vision.cursor,s.frame)
            same=cursor and abs(cursor['x']-p['x'])<=8 and abs(cursor['y']-p['y'])<=8
            count=vision.loot_messages(s.frame)
            messages=messages+1 if count>before else 0
            skinnable=skinnable+1 if same and cursor['kind']=='skin' else 0
            hands=hands+1 if same and cursor['kind']=='hand' else 0
            if messages>=2 or (interaction=='loot' and skinnable>=3):
                ctx.checkpoint['uncertain_transaction']=False
                reason='new_loot_messages_two_frames' if messages>=2 else 'loot_to_skin_cursor_three_frames'
                return Result('completed',reason,dict(interaction_confirmed=True,corpse_empty_verified=False),(s.sequence,))
            if hands>=3:
                break
        receipts=[]
        for n in range(2):
            s=yield from fresh(ctx,.8,True)
            cursor=yield from ctx.work(vision.cursor,s.frame)
            if not cursor or cursor['kind']!='hand' or abs(cursor['x']-p['x'])>8 or abs(cursor['y']-p['y'])>8:
                break
            receipts.append((yield from ctx.work(reader,s.frame,folder,f'after-{n}')))
        confirmed=(len(receipts)==2 and all(increased(baseline,r) for r in receipts)) if interaction=='skin' else confirmed_pair(baseline,receipts)
        if confirmed:
            ctx.checkpoint['uncertain_transaction']=False
            return Result('completed','new_skin_material_ocr_two_frames' if interaction=='skin' else 'new_coin_receipt_two_frames',
                          dict(interaction_confirmed=True,corpse_empty_verified=False),(str(folder),s.sequence))
        return Result('failed','no_new_loot_messages')
    return Result('skipped','no_verified_corpse')


def roam(ctx, direction='right'):
    result=yield from recover(ctx)
    if result.status!='completed':
        return result
    s=yield from fresh(ctx,.95,True)
    if s.observation.target:
        yield from tap(ctx,41,80,'clear_target',.95,True)
    yield from fresh(ctx,.95,True,True)
    yield from tap(ctx,80 if direction=='left' else 79,120,'patrol_turn',.95,True)
    for _ in range(2):
        yield from tap(ctx,26,500,'patrol_move',.95,True)
    s=yield from fresh(ctx,.95,True)
    return Result('completed','STEP_COMPLETE',{},(s.sequence,))


def location(ctx, folder):
    folder=Path(folder)/ctx.task
    folder.mkdir(parents=True,exist_ok=True)
    values=[]
    for _ in range(2):
        s=yield from fresh(ctx,.95,True,True)
        point=yield from ctx.work(coordinate,s.frame,folder,f'{s.sequence}')
        if (not isinstance(point,(tuple,list)) or len(point)!=2 or
                any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=100 for v in point)):
            raise GuardFailed('invalid_coordinate')
        values.append(tuple(point))
    if values[0]!=values[1]:
        raise GuardFailed('stationary_coordinate_disagreement')
    ctx.record('position_confirmed',task=ctx.task,frame=s.sequence,position=values[-1],evidence=str(folder))
    ctx.checkpoint['position_evidence']=(s.sequence,str(folder))
    return tuple(values[-1])


def navigate(ctx, folder, points, on_waypoint=None, checkpoint_key='waypoint',max_steps=120):
    if type(max_steps) is not int or not 1<=max_steps<=120:
        raise GuardFailed('invalid_navigation_step_limit')
    if (not points or any(not isinstance(p,(list,tuple)) or len(p)!=2 or
            any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=100 for v in p) for p in points)):
        raise GuardFailed('invalid_navigation_route')
    ctx.phase='navigation'
    # A threat may interrupt a detour at any yield. Resume by returning to its
    # saved route junction first, with the original budget and separate progress.
    pending=ctx.checkpoint.get('mining_return') if checkpoint_key=='waypoint' else None
    if pending:
        if ctx.clock()>=pending['deadline']:
            return Result('failed','mining_return_budget_expired')
        deadline=ctx.deadline
        ctx.deadline=min(deadline,pending['deadline'])
        try:
            returned=yield from navigate(ctx,folder,[pending['position']],checkpoint_key='mining_waypoint',max_steps=max_steps)
        finally:
            ctx.deadline=deadline
        if returned.status!='completed':
            return Result('failed','mining_return_failed',dict(return_reason=returned.reason))
        ctx.checkpoint.pop('mining_return',None)
        ctx.checkpoint.pop('mining_waypoint',None)
    index=ctx.checkpoint.get(checkpoint_key,0)
    if not isinstance(index,int) or not 0<=index<=len(points):
        raise GuardFailed('invalid_navigation_checkpoint')
    # Even a completed waypoint list must revalidate its terminal location.
    index=min(index,len(points)-1)
    s=yield from fresh(ctx,.95,True)
    if s.observation.target:
        yield from tap(ctx,41,80,'clear_navigation_target',.95,True)
    previous=yield from location(ctx,folder)
    # Resume always recomputes heading from new displacement.
    heading=None
    recent=[previous]
    for index in range(index,len(points)):
        target=tuple(points[index])
        ctx.record('navigation_target',task=ctx.task,waypoint=index,target=target,position=previous)
        best=math.dist(previous,target)
        stuck=unproductive=0
        for _ in range(max_steps):
            if math.dist(previous,target)<=.2:
                ctx.checkpoint[checkpoint_key]=index+1
                if on_waypoint:
                    detour=yield from on_waypoint(ctx,previous)
                    if detour and detour.status=='failed':
                        return detour
                    previous=yield from location(ctx,folder)
                    heading=None
                    if math.dist(previous,target)>.2:
                        ctx.checkpoint[checkpoint_key]=index
                        continue
                break
            if heading is not None:
                error=steering(previous,heading,target)
                if abs(error)>.35:
                    yield from tap(ctx,79 if error>0 else 80,min(200,max(40,round(abs(error)/math.pi*1000))),
                                   'navigation_turn',.95,True)
            for _ in range(2):
                yield from tap(ctx,26,500,'navigation_move',.95,True)
            current=yield from location(ctx,folder)
            distance=math.dist(current,previous)
            if distance>.6:
                return Result('failed','implausible_displacement')
            recent=(recent+[current])[-4:]
            if oscillating(recent):
                return Result('failed','oscillating_route')
            stuck=stuck+1 if distance<.07 else 0
            if stuck>=2:
                return Result('failed','route_obstructed')
            if distance>=.07:
                heading=math.atan2(current[1]-previous[1],current[0]-previous[0])
            remaining=math.dist(current,target)
            if remaining<best-.05:
                best,unproductive=remaining,0
            else:
                unproductive+=1
            if unproductive>=12:
                return Result('failed','no_navigation_progress')
            previous=current
            if on_waypoint:
                detour=yield from on_waypoint(ctx,previous)
                if detour and detour.status=='failed':
                    return detour
                if detour and detour.facts.get('returned'):
                    previous=yield from location(ctx,folder)
                    heading=None
            if math.dist(previous,target)<=.2:
                ctx.checkpoint[checkpoint_key]=index+1
                break
        else:
            return Result('failed','navigation_step_limit')
    return Result('completed','ARRIVED',dict(position=previous,target=points[-1]),
                  tuple(ctx.checkpoint.get('position_evidence',())))
