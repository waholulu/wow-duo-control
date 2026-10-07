"""Cooperative existing skills. No hardware, subprocess orchestration, or sleeps."""
import math
from dataclasses import replace
from pathlib import Path
import time
from interaction_vision import BagVision
from interaction_vision import LootVision
from control_policy import Policy
from threat_settlement import QuietPeriod, POST_KILL_WAIT_SECONDS
from threat_state import observe_threat
from navigation_coordinate import coordinate
from navigate_local import steering, oscillating
from coin_receipt import read_coin_receipts, read_loot_receipts, confirmed_pair
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
    for attempt in range(4):
        s = yield from fresh(ctx, minimum_hp, peaceful)
        try:
            yield from ctx.act(s, 'tap', (key,ms), reason, minimum_hp, peaceful)
            break
        except GuardFailed as exc:
            if str(exc)!='stale_frame_before_input' or attempt>=3:raise
            ctx.record('tap_action_recomputed',task=ctx.task,reason=reason,
                       rejected_frame=s.sequence,attempt=attempt+1)
            yield from ctx.pause(.05)
    yield from ctx.pause(ms/1000+.12)


def move_pointer(ctx, vision, x, y, bounds=(375,150,1625,885), expected=None, tolerance=8,
                 allow_unconfirmed_absolute=False):
    if not bounds[0]<=x<=bounds[2] or not bounds[1]<=y<=bounds[3]:
        raise GuardFailed('pointer_destination_outside_calibration')
    control=getattr(ctx,'vision_profile',{}).get('cursor_control',{})
    max_step=min(60,max(1,control.get('max_step',25)))
    settle=max(.1,control.get('settle_seconds',.3))
    missing=0
    reacquire_moves=((12,0),(-12,0),(0,12),(0,-12))
    absolute_tried=False
    last_move=None
    for _ in range(60):
        s = yield from fresh(ctx, .8, True)
        cursor = yield from ctx.work(vision.cursor, s.frame)
        ctx.record('pointer_reading',task=ctx.task,frame=s.sequence,cursor=cursor,destination=[x,y])
        if cursor is None:
            missing+=1
            absolute=getattr(ctx,'vision_profile',{}).get('absolute_cursor_reacquire')
            if absolute and not absolute_tried:
                screen=absolute.get('screen_size',[1920,1080])
                if (not isinstance(screen,list) or len(screen)!=2
                        or not all(type(v) is int and 320<=v<=8192 for v in screen)):
                    raise GuardFailed('invalid_absolute_cursor_calibration')
                transform=s.calibration
                if len(transform)==3:
                    sx,tx,ty=transform;sy=sx
                elif len(transform)==4:
                    sx,sy,tx,ty=transform
                else:
                    raise GuardFailed('invalid_absolute_cursor_calibration')
                raw_x,raw_y=round(x*sx+tx),round(y*sy+ty)
                if not 0<=raw_x<screen[0] or not 0<=raw_y<screen[1]:
                    raise GuardFailed('absolute_cursor_destination_outside_screen')
                if ctx.clock()-s.captured_at>.45:
                    s=yield from fresh(ctx,.8,True)
                yield from ctx.act(s,'move_to',(raw_x,raw_y,*screen),
                                   'reacquire_pointer_absolute',.8,True)
                absolute_tried=True
                missing=0
                yield from ctx.pause(settle)
                if allow_unconfirmed_absolute:
                    ctx.record('pointer_absolute_park_unconfirmed',task=ctx.task,
                               destination=[x,y],raw_destination=[raw_x,raw_y])
                    return None
                continue
            if missing>len(reacquire_moves):
                raise GuardFailed('cursor_not_located')
            # A stationary hardware cursor can blend into a particular world
            # patch or remain hidden until it moves.  Probe a tiny closed loop:
            # it never clicks, is admitted only on a fresh peaceful frame, and
            # returns to the starting point if recognition never recovers.
            if ctx.clock()-s.captured_at>.45:
                s=yield from fresh(ctx,.8,True)
            yield from ctx.act(s,'move',reacquire_moves[missing-1],
                               'reacquire_pointer',.8,True)
            yield from ctx.pause(settle)
            continue
        missing=0
        if ctx.clock()-s.captured_at>.45:
            continue
        if expected in ('loot','skin') and cursor['kind']==expected:
            # Capture latency may show the cursor passing over a corpse while
            # the previous mouse command is still taking effect on the PC.
            yield from ctx.pause(.3)
            settled=yield from fresh(ctx,.8,True)
            confirmed=yield from ctx.work(vision.cursor,settled.frame)
            if (confirmed and confirmed['kind']==expected
                    and abs(confirmed['x']-cursor['x'])<=4
                    and abs(confirmed['y']-cursor['y'])<=4):
                # Cursor recognition can occasionally exceed the input frame's
                # 500 ms authority window.  Never return that stale snapshot to
                # the click caller. Reacquire once without another settle delay;
                # otherwise every fresh loot frame can be delayed into staleness.
                if ctx.clock()-settled.captured_at>.45:
                    authorized=yield from fresh(ctx,.8,True)
                    current=yield from ctx.work(vision.cursor,authorized.frame)
                    ctx.record('pointer_authority_reacquired',task=ctx.task,
                               frame=authorized.sequence,cursor=current)
                    if (current and current['kind']==expected
                            and abs(current['x']-confirmed['x'])<=4
                            and abs(current['y']-confirmed['y'])<=4
                            and ctx.clock()-authorized.captured_at<=.45):
                        return authorized,current
                    continue
                return settled,confirmed
            continue
        dx,dy = x-cursor['x'], y-cursor['y']
        if abs(dx)<=tolerance and abs(dy)<=tolerance:
            if expected and cursor['kind']!=expected:
                return None
            return s,cursor
        if last_move and abs(cursor['x']-last_move[0])+abs(cursor['y']-last_move[1])<2 and ctx.clock()-last_move[2]<.6:
            yield from ctx.pause(.1)
            continue
        step = tuple(max(-max_step,min(max_step,round(v/3))) for v in (dx,dy))
        yield from ctx.act(s,'move',step,'position_pointer',.8,True)
        last_move=(cursor['x'],cursor['y'],ctx.clock())
        yield from ctx.pause(settle)
    raise GuardFailed('pointer_convergence_limit')


def probe_absolute_loot_point(ctx, vision, x, y):
    """Jump to one corpse proposal, then verify the actual cursor locally."""
    spec=getattr(ctx,'vision_profile',{}).get('absolute_cursor_reacquire')
    if not spec:return True  # Older profiles retain the bounded relative path.
    width,height=spec.get('screen_size',[1920,1080])
    if not all(type(v) is int and 320<=v<=8192 for v in (width,height)):
        raise GuardFailed('invalid_absolute_cursor_calibration')
    s=yield from fresh(ctx,.8,True)
    transform=s.calibration
    if len(transform)==3:
        sx,tx,ty=transform;sy=sx
    elif len(transform)==4:
        sx,sy,tx,ty=transform
    else:
        raise GuardFailed('invalid_absolute_cursor_calibration')
    raw_x,raw_y=round(x*sx+tx),round(y*sy+ty)
    if not 0<=raw_x<width or not 0<=raw_y<height:
        raise GuardFailed('absolute_cursor_destination_outside_screen')
    yield from ctx.act(s,'move_to',(raw_x,raw_y,width,height),
                       'probe_corpse_absolute',.8,True)
    yield from ctx.pause(.2)
    left,top=max(375,x-80),max(150,y-80)
    right,bottom=min(1625,x+80),min(885,y+80)
    for attempt in range(3):
        current=yield from fresh(ctx,.8,True)
        cursor=yield from ctx.work(vision.cursor,current.frame,
                                   (left,top,right-left,bottom-top))
        near=bool(cursor and abs(cursor['x']-x)<=20 and abs(cursor['y']-y)<=20)
        ctx.record('corpse_absolute_probe',task=ctx.task,frame=current.sequence,
                   destination=[x,y],cursor=cursor,near=near,attempt=attempt+1)
        if near:return True
        if attempt<2:yield from ctx.pause(.15)
    return False


def select_chat(ctx,name):
    spec=getattr(ctx,'vision_profile',{}).get('chat_tabs',{}).get(name)
    if not spec:return Result('completed','chat_tab_not_configured')
    from chat_tabs import selected
    if spec.get('persistent'):
        # The visible panel can be checked while defending. Only UI mutations
        # below require full health and peace.
        s=yield from fresh(ctx)
        visible=yield from ctx.work(selected,s.frame,spec)
        if visible:return Result('completed','chat_panel_visible')
        compact=spec.get('compact') if name=='general' else None
        if compact and (yield from ctx.work(selected,s.frame,compact)):
            return Result('completed','chat_compact_receipt_channel')
        return Result('failed','chat_panel_layout_unconfirmed')
    s=yield from fresh(ctx,.95,True)
    if (yield from ctx.work(selected,s.frame,spec)):return Result('completed','chat_tab_selected')
    vision=LootVision(getattr(ctx,'vision_profile',{}).get('cursor_templates'))
    found=yield from move_pointer(ctx,vision,*spec['point'])
    if not found:return Result('failed','chat_tab_cursor_unconfirmed')
    s,_=found
    yield from ctx.act(s,'click',(0,),'select_chat_'+name,.95,True)
    yield from ctx.pause(.4)
    yield from move_pointer(ctx,vision,420,620)
    for _ in range(4):
        s=yield from fresh(ctx,.95,True)
        if (yield from ctx.work(selected,s.frame,spec)):return Result('completed','chat_tab_selected')
        yield from ctx.pause(.15)
    return Result('failed','chat_tab_unconfirmed')


def combat(ctx, scheduler, seconds=90, xp_limit=1):
    scheduler.corpse_hint=None
    if getattr(ctx,'vision_profile',{}).get('chat_tabs'):
        chat=yield from select_chat(ctx,'combat')
        if chat.status!='completed':return chat
    policy = Policy(skip_unknown=True, **getattr(scheduler, 'combat_options', {}))
    policy.buff_cast_at=getattr(scheduler,'buff_cast_at',None)
    policy.opener_last_at=getattr(scheduler,'opener_last_at',None)
    policy.defensive=ctx.required_target is not None
    evidence=getattr(getattr(scheduler,'source',None),'combat_evidence',None)
    started = ctx.clock()
    last_engaged = started
    target_track=attack_track=None
    finishing_since=None
    finish_cleared=False
    finish_quiet=QuietPeriod()
    finish_initial_hp=None
    log_loss_since=None
    log_target_names=[]
    log_engaged_at=None
    rejected_actions=0
    while True:
        s = yield from fresh(ctx)
        if not s.combat_known:
            raise GuardFailed('combat_status_unknown')
        o,now = s.observation,ctx.clock()
        if evidence:
            # Runtime XP comes from the capture-side journal, never a transient
            # boolean delivered (or skipped) by the cooperative scheduler.
            policy.xp_events=evidence.facts(ctx.task)['xp_events']
            ctx.checkpoint['confirmed_facts']=evidence.facts(ctx.task)
            o=replace(o,xp_visible=False)
        threat=observe_threat(ctx,s,now)
        threat_failure=('additional_attacker_suspected' if threat.additional_suspected and threat.visual
                        else 'damage_without_combat_indicator' if threat.damaged and not o.in_combat else None)
        if threat_failure:
            # A threat on the XP frame must not erase the independently earned kill.
            confirmed=policy.xp_events+int(o.xp_visible and not policy.previous_xp and now<=policy.pending_xp_until)
            return Result('failed',threat_failure,
                          dict(xp_events=confirmed,review_required=True,
                               attacker_hints=list(threat.sources)),(s.sequence,))
        if (s.target_track is not None and target_track is not None
                and s.target_track!=target_track and attack_track is not None and o.in_combat):
            ctx.record('evidence_request',reason='target_changed_under_threat',frame=s.sequence,
                       previous_track=target_track,current_track=s.target_track)
            return Result('failed','target_changed_under_threat',dict(xp_events=policy.xp_events), (s.sequence,))
        if s.target_track is not None and s.target_track!=target_track:
            # Preserve XP/rest counters, but never transfer damage or attack
            # history to another observable target continuity track.
            target_track=s.target_track
            attack_track=None
            policy.reset_for_target_change(now)
        if (o.target and o.target_allowed and o.target_point
                and (not xp_limit or policy.xp_events<xp_limit)
                and 0<=now-s.captured_at<=.5):
            from workflow_state import CorpseHint
            scheduler.corpse_hint=CorpseHint(s,o.target_point)
        log_reader=getattr(getattr(scheduler,'source',None),'combat_log',None)
        if o.target and o.target_allowed:
            aliases=getattr(ctx,'vision_profile',{}).get('combat_log_target_names',{})
            log_target_names=aliases.get(s.target_label,[])
            log_loss_since=None
        # XP is the independent kill confirmation.  Once its bounded counter
        # has reached this run's limit, a still-visible kill line must not turn
        # the same confirmed kill back into ``combat_log_kill_unconfirmed``.
        # Death text and post-kill threat evidence remain authoritative below.
        finishing = bool(xp_limit and policy.xp_events >= xp_limit)
        if not o.target and log_reader and log_engaged_at is not None:
            from combat_log_cv import loss_decision
            if log_loss_since is None:log_loss_since=now
            rows=log_reader.latest(now,log_engaged_at,log_target_names)
            decision=loss_decision(rows,o.player_hp>=.85,o.in_combat,now-log_loss_since)
            if decision:
                ctx.record('combat_log_decision',frame=s.sequence,decision=decision,evidence=rows,
                           player_hp=o.player_hp,in_combat=o.in_combat)
                kill_already_confirmed=(finishing and decision in (
                    'wait_for_kill_confirmation','combat_log_kill_unconfirmed'))
                if kill_already_confirmed:
                    ctx.record('combat_log_confirmation_reconciled',frame=s.sequence,
                               decision=decision,xp_events=policy.xp_events)
                elif decision.startswith('wait_') and not o.xp_visible:
                    # Keep existing autoattack only. Never Tab, move, or fabricate
                    # a target from OCR. XP/health guards remain authoritative.
                    yield from ctx.pause(.15)
                    continue
                elif not decision.startswith('wait_'):
                    if decision=='combat_log_death_review':
                        source=getattr(scheduler,'source',None)
                        callback=getattr(getattr(source,'store',None),'death_review_callback',None)
                        if callback:
                            source.death_review_pending=True
                            callback('fresh_visual_log_death_text')
                    return Result('failed',decision,dict(xp_events=policy.xp_events,review_required=True),(s.sequence,))
        # A cleanup self-selection or a new full-health portrait must not skip
        # post-kill threat settlement and report peace while incoming text is fresh.
        if finishing:
            # Defense is complete: subsequent actions only cancel movement and
            # clear attack. Binding them to the vanished enemy would reject cleanup.
            ctx.required_target=None
            if finishing_since is None:
                finishing_since=now
                finish_initial_hp=o.player_hp
            incoming_rows=log_reader.latest(now,finishing_since-3,[]) if log_reader else []
            fresh_incoming=any(e['kind']=='incoming_damage_text' for e in incoming_rows)
            settled=finish_quiet.step(now,s.sequence,
                valid=o.valid and s.combat_known and 0<=now-s.captured_at<=.5,
                threat=threat.active or fresh_incoming)
            if threat.damaged or o.player_hp<finish_initial_hp-.02:
                return Result('failed','post_kill_damage_continues',dict(xp_events=policy.xp_events),(s.sequence,))
            if settled:
                return Result('completed','xp_limit_out_of_combat',
                              dict(xp_events=policy.xp_events,out_of_combat=True,
                                   rest_cycles=policy.rest_cycles,identity_verified=False),(s.sequence,))
            if not finish_cleared:
                # With no selected target Escape opens the menu. A short backstep
                # cancels CTM while the portrait's combat indication settles.
                yield from ctx.act(s,'tap',(22,20),'cancel_movement_after_kill',.4)
                options=getattr(ctx,'recovery_options',{})
                if options.get('verified') and options.get('self_key'):
                    yield from tap(ctx,options['self_key'],80,'clear_attack_by_self_selection',minimum_hp=.4)
                    check=yield from fresh(ctx,.4)
                    if check.observation.target:
                        yield from ctx.act(check,'tap',(41,80),'clear_confirmed_self_target',.4)
                finish_cleared=True
            if now-finishing_since>=POST_KILL_WAIT_SECONDS or o.player_hp<.55:
                return Result('failed','post_kill_combat_unresolved',
                              dict(xp_events=policy.xp_events,out_of_combat=not o.in_combat),(s.sequence,))
            yield from ctx.pause(.15)
            continue
        before_decision=policy.action_checkpoint()
        before_engaged=log_engaged_at
        effect_before=getattr(policy,'last_opener_effect',None)
        cast_before=getattr(policy,'last_cast_effect',None)
        if log_reader and getattr(policy,'opener_pending_at',None) is not None:
            policy.observe_offensive_log(log_reader.latest(
                now,policy.opener_pending_at,log_target_names,
                include_unanchored_damage=True),now)
        action = policy.step(o,now,now-s.captured_at)
        effect_after=getattr(policy,'last_opener_effect',None)
        if effect_after!=effect_before:
            ctx.record('offensive_effect_observed',task=ctx.task,frame=s.sequence,
                       state=effect_after[2],reason=effect_after[1],
                       target_track=s.target_track)
        cast_after=getattr(policy,'last_cast_effect',None)
        if cast_after!=cast_before:
            ctx.record('cast_effect_observed',task=ctx.task,frame=s.sequence,state=cast_after[1],
                       casting_known=o.casting_known,target_track=s.target_track)
        if action and action.reason in ('ranged_opener','interact_target','attack') and log_engaged_at is None:
            log_engaged_at=now
        ctx.checkpoint['confirmed_facts']={'xp_events':policy.xp_events}
        if xp_limit and policy.xp_events>=xp_limit and (not o.target or o.target_hp<=.02):
            # The XP frame itself must enter the same quiet-period verification;
            # a transient peaceful portrait is not enough to skip the guard.
            ctx.record('combat_state',task=ctx.task,state='POST_KILL_VERIFY',
                       stop_reason=None,xp_events=policy.xp_events,frame=s.sequence)
            yield from ctx.pause(.15)
            continue
        if xp_limit and policy.xp_events>=xp_limit and action and action.reason in (
                'select_or_scan','skip_unknown_without_attack','skip_unseen_target'):
            action=None
        if o.in_combat or policy.damaged_target:
            last_engaged = now
        if policy.damaged_target and target_track is not None and attack_track==target_track:
            scheduler.note_engagement(s)
        if now-last_engaged>=30 and not o.in_combat and (policy.last_attempt is None or now-policy.last_attempt>4):
            policy.stop('no_targets_found')
        if o.in_combat and o.player_hp<.55 and not policy.can_finish_melee(o,now):
            policy.stop('combat_health_retreat')
        if xp_limit and policy.xp_events>=xp_limit and not o.in_combat:
            policy.stop('xp_limit_out_of_combat')
        if now-started>=seconds and not policy.stopped:
            if not o.in_combat:
                policy.stop('duration_limit_out_of_combat')
            elif now-started>=seconds+45:
                policy.stop('duration_combat_grace_exhausted')
            if action and action.reason!='attack':
                action=None
        ctx.record('combat_state', task=ctx.task, state=policy.state, stop_reason=policy.stopped,
                   xp_events=policy.xp_events, frame=s.sequence)
        if policy.stopped:
            if policy.stopped not in ('xp_limit_out_of_combat','no_targets_found','duration_limit_out_of_combat'):
                ctx.record('evidence_request', reason=policy.stopped, frame=s.sequence,
                           target_track=s.target_track, player_hp=o.player_hp, target_hp=o.target_hp,
                           in_combat=o.in_combat, policy_state=policy.state)
            completed = policy.stopped=='xp_limit_out_of_combat'
            return Result('completed' if completed else 'failed', policy.stopped,
                          dict(xp_events=policy.xp_events, out_of_combat=not o.in_combat,
                               rest_cycles=policy.rest_cycles, identity_verified=False), (s.sequence,))
        if action:
            try:
                yield from ctx.act(s,'tap',(action.key,action.milliseconds),action.reason,.55 if o.in_combat else .4)
            except GuardFailed as exc:
                if str(exc) not in ('stale_frame_before_input','unusable_live_view') or rejected_actions>=8:raise
                policy.reject_action(before_decision)
                log_engaged_at=before_engaged;rejected_actions+=1
                ctx.record('combat_action_recomputed',reason=str(exc),frame=s.sequence)
                continue
            rejected_actions=0
            if action.reason=='attack' and not policy.attack_once:
                policy.cast_pending_at=ctx.clock()
                policy.ready_at=policy.cast_pending_at+policy.attack_interval
            if action.reason=='maintain_required_buff':
                policy.buff_cast_at=scheduler.buff_cast_at=ctx.clock()
                policy.ready_at=policy.buff_cast_at+policy.precombat_wait
                ctx.record('buff_timer_started',task=ctx.task,cast_at_monotonic=policy.buff_cast_at,
                           refresh_at_monotonic=policy.buff_cast_at+policy.buff_refresh_seconds,
                           basis='input_acknowledged_timer',frame=s.sequence)
            if action.reason in ('ranged_opener','cooldown_strike'):
                policy.opener_last_at=scheduler.opener_last_at=ctx.clock()
                policy.opener_pending_at=policy.opener_last_at
                ctx.record('offensive_cooldown_started',task=ctx.task,reason=action.reason,
                           cooldown_until=policy.opener_last_at+policy.opener_cooldown,seal_consumed=False)
            if action.reason in ('attack','interact_target','ranged_opener'):
                attack_track=target_track


def maintain_self_buff(ctx,scheduler):
    spec=getattr(scheduler,'self_buff_options',{})
    if not spec.get('verified'):return Result('completed','self_buff_disabled')

    state_path=getattr(scheduler,'self_buff_state_path',None)
    if state_path:
        from buff_timer import remaining
        left=remaining(state_path,spec['key'],spec['refresh_seconds'],getattr(scheduler,'self_buff_gate_path',None))
        if left>0:return Result('completed','self_buff_persisted_timer_current',dict(remaining_seconds=left))
    last=getattr(scheduler,'self_buff_at',None)
    if last is not None and ctx.clock()-last<spec['refresh_seconds']:
        return Result('completed','self_buff_timer_current')
    yield from tap(ctx,spec['self_key'],80,'select_self_for_buff',.95,True)
    s=yield from fresh(ctx,.95,True)
    before=s.observation.player_mana
    if before<.4:return Result('failed','self_buff_mana_reserve')
    yield from tap(ctx,spec['key'],80,'maintain_self_buff',.95,True)
    scheduler.self_buff_at=ctx.clock()
    if state_path:
        from buff_timer import save
        save(state_path,spec['key'])
    ctx.record('self_buff_timer_started',task=ctx.task,refresh_at=scheduler.self_buff_at+spec['refresh_seconds'])
    yield from ctx.pause(1.6)
    s=yield from fresh(ctx,.95,True)
    if s.observation.target:
        yield from ctx.act(s,'tap',(41,80),'clear_self_after_buff',.95,True)
        yield from ctx.pause(.3)
    return Result('completed','self_buff_applied_by_timer',dict(refresh_seconds=spec['refresh_seconds']))


def disengage_unreachable(ctx):
    """Cancel a failed pull, then verify peace before returning to patrol."""
    s=yield from fresh(ctx,.8)
    if not s.combat_known or observe_threat(ctx,s).active:
        return Result('failed','threat_after_failed_pull')
    initial_hp=s.observation.player_hp
    if s.observation.target:
        yield from ctx.act(s,'tap',(41,80),'clear_unreachable_target',.8,True)
    yield from ctx.pause(.5)
    clear=0
    for _ in range(12):
        s=yield from fresh(ctx,.8)
        o=s.observation
        if not s.combat_known or o.player_hp < initial_hp-.02:
            return Result('failed','threat_after_failed_pull')
        clear=clear+1 if not o.in_combat and not o.target else 0
        if clear>=3:
            return Result('completed','failed_pull_cleared',dict(out_of_combat=True),(s.sequence,))
        yield from ctx.pause(.2)
    return Result('failed','still_in_combat_after_clear')


def escape(ctx):
    # Phase-local peace duration, shared injury/source history.
    quiet=QuietPeriod(seconds=5)
    moves=[(79,500)]*2+[(26,500)]*24
    step=0
    final_observation_since=None
    while True:
        s = yield from fresh(ctx)
        if not s.combat_known:
            return Result('failed','combat_unknown')
        if step>=len(moves):
            if final_observation_since is None:final_observation_since=ctx.clock()
            if ctx.clock()-final_observation_since>=8:
                return Result('failed','ESCAPE_LIMIT')
        threat=observe_threat(ctx,s)
        if quiet.step(ctx.clock(),s.sequence,valid=threat.fresh,threat=threat.active):
            return Result('completed','ESCAPED',dict(out_of_combat=True,hp=s.observation.player_hp),(s.sequence,))
        if not threat.visual:
            # Text can prevent a false success but cannot authorize movement.
            yield from ctx.pause(.2)
            continue
        if step>=len(moves):
            # Verify the effect of the final movement before declaring failure.
            yield from ctx.pause(.2)
            continue
        key,ms=moves[step];step+=1
        yield from ctx.act(s,'tap',(key,ms),'escape')
        yield from ctx.pause(ms/1000+.12)


def prepare(ctx,scheduler):
    """One pull-readiness gate; heal and restore resources only when needed."""
    ready=yield from recover(ctx)
    if ready.status!='completed':return ready
    buff=yield from maintain_self_buff(ctx,scheduler)
    if buff.status!='completed':return buff
    if buff.reason=='self_buff_applied_by_timer':
        ready=yield from recover(ctx)
        if ready.status!='completed':return ready
    # A prior bounded failure may leave a harmless world target selected.
    # Clear it only after peace/full-health readiness is established so the
    # next combat skill cannot inherit or attack a third-party-damaged target.
    s=yield from fresh(ctx,.95,True)
    if s.observation.target:
        yield from ctx.act(s,'tap',(41,80),'clear_target_before_patrol',.95,True)
        yield from ctx.pause(.25)
        clear=0
        for _ in range(5):
            s=yield from fresh(ctx,.95,True)
            clear=clear+1 if not s.observation.target else 0
            if clear>=3:
                return Result('completed','ready_target_cleared',dict(out_of_combat=True),(s.sequence,))
            yield from ctx.pause(.15)
        return Result('failed','ready_target_not_cleared')
    return ready


def recover(ctx,health_only=False):
    count=0
    options=getattr(ctx,'recovery_options',{})
    casts=0
    while True:
        s=yield from fresh(ctx)
        o=s.observation
        threat=observe_threat(ctx,s)
        if not threat.fresh or threat.active:
            return Result('failed','combat_restarted_during_recovery')
        count=count+1 if o.player_hp>=.95 and (health_only or o.player_mana>=options.get('mana_target',.85)) else 0
        if count>=3:
            return Result('completed','recovered',dict(hp=o.player_hp,mana=o.player_mana,heal_casts=casts),(s.sequence,))
        if (options.get('verified') and o.player_hp<.85 and o.player_mana>=.4
                and casts<2 and not o.casting):
            before_hp,before_mana=o.player_hp,o.player_mana
            yield from tap(ctx,options['self_key'],80,'select_self_for_heal',peaceful=True)
            yield from tap(ctx,options['heal_key'],80,'recovery_heal',peaceful=True)
            casts+=1
            # Observe throughout cast; do not mask a renewed attack with a blind wait.
            until=ctx.clock()+options['cast_seconds']+.8
            while ctx.clock()<until:
                s=yield from fresh(ctx)
                threat=observe_threat(ctx,s)
                if not threat.fresh or threat.active:
                    return Result('failed','combat_restarted_during_recovery')
                yield from ctx.pause(.15)
            s=yield from fresh(ctx)
            ctx.record('recovery_heal_observed',task=ctx.task,frame=s.sequence,
                       before_hp=before_hp,after_hp=s.observation.player_hp,
                       before_mana=before_mana,after_mana=s.observation.player_mana)
            yield from tap(ctx,41,80,'clear_self_after_heal',peaceful=True)
            if s.observation.player_hp<=before_hp+.05:
                return Result('failed','recovery_heal_unconfirmed',dict(heal_casts=casts))
        yield from ctx.pause(.2)


def backpack(ctx):
    profile=getattr(ctx,'vision_profile',{})
    bag, cursor = BagVision(profile.get('backpack')), LootVision(profile.get('cursor_templates'))
    ctx.phase='backpack'
    yield from move_pointer(ctx,cursor,*profile.get('backpack_pointer_park',[800,250]),
                            expected='hand',allow_unconfirmed_absolute=True)
    yield from ctx.pause(.4)
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
    closed=False
    for _ in range(6):
        s=yield from fresh(ctx,.8,True)
        closed=not (yield from ctx.work(bag.observe,s.frame))['open']
        if closed: break
        yield from ctx.pause(.15)
    if stable<3 or not closed:
        return Result('failed','backpack_unconfirmed',dict(closed_verified=closed))
    return Result('completed','backpack_confirmed',dict(reading,closed_verified=True), (observed_at,s.sequence))


def loot_search_order(candidates):
    """Try temporal sparkles and corpse templates before the fallback grid.

    A sparkle only proposes a cursor check; it never authorizes a click.
    """
    distance=lambda p:abs(p['x']-1005)+abs(p['y']-590)
    temporal=sorted((p for p in candidates if p.get('source')=='temporal_sparkles'),key=distance)
    strong=sorted((p for p in candidates if p.get('score',0)>=.65 and p.get('source')!='temporal_sparkles'),key=distance)
    weak=sorted((p for p in candidates if p.get('score',0)<.65),key=distance)
    grid=[dict(x=x,y=y) for y in (580,530,620,660,710,800,850)
          for x in (1005,955,1055,895,1115)]
    return temporal+strong+grid+weak


def loot(ctx, folder, interaction='loot', point=None):
    chat=yield from select_chat(ctx,'general')
    if chat.status!='completed':return chat
    result=yield from loot_contents(ctx,folder,interaction,point)
    if result.status=='completed' and getattr(ctx,'vision_profile',{}).get('chat_tabs'):
        yield from tap(ctx,22,20,'cancel_click_to_move',.8,True)
    restored=yield from select_chat(ctx,'combat')
    if restored.status!='completed':return restored
    return result


def loot_contents(ctx, folder, interaction='loot', point=None, vision=None):
    profile=getattr(ctx,'vision_profile',{})
    if vision is None:
        vision=LootVision(profile.get('cursor_templates'),profile.get('loot_message_template'))
    hint=getattr(ctx,'corpse_hint',None)
    if interaction=='loot' and point is None and hint is not None:
        ctx.corpse_hint=None  # Consume once; a failed hint falls back to normal CV search.
        s=yield from fresh(ctx,.95,True)
        points=hint.points(s,ctx.clock())
        excluded=getattr(ctx,'vision_profile',{}).get('interaction_exclude_rois',[])
        for x,y in points:
            current=yield from fresh(ctx,.95,True)
            if (x,y) not in hint.points(current,ctx.clock()):break
            if any(left<=x<left+w and top<=y<top+h for left,top,w,h in excluded):continue
            ctx.record('corpse_hint_probe',task=ctx.task,point=[x,y],hint_at=hint.at)
            result=yield from loot_contents(ctx,folder,interaction,point=(x,y),vision=vision)
            if result.status!='skipped':return result
    folder=Path(folder)/ctx.task
    folder.mkdir(parents=True,exist_ok=True)
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
        points=[]
        sparkle_frames=[]
        # Loot sparkles blink; a single initial frame may miss the nearby corpse.
        for _ in range(4):
            s=yield from fresh(ctx,.8,True)
            sparkle_frames.append(s.frame)
            candidates=yield from ctx.work(vision.candidates,s.frame)
            for p in candidates:
                if any(x<=p['x']<x+w and y<=p['y']<y+h
                       for x,y,w,h in profile.get('interaction_exclude_rois',[])):
                    continue
                if all(abs(p['x']-q['x'])+abs(p['y']-q['y'])>25 for q in points):
                    points.append(p)
            yield from ctx.pause(.15)
        temporal=yield from ctx.work(vision.sparkle_candidates,sparkle_frames)
        excluded=profile.get('interaction_exclude_rois',[])
        temporal=[p for p in temporal if not any(x<=p['x']<x+w and y<=p['y']<y+h for x,y,w,h in excluded)]
        ctx.record('corpse_sparkle_candidates',task=ctx.task,candidates=temporal,samples=len(sparkle_frames))
        points=loot_search_order(temporal+points)
    for p in points:
        if interaction=='loot' and not (yield from probe_absolute_loot_point(ctx,vision,p['x'],p['y'])):
            continue
        found=yield from move_pointer(ctx,vision,p['x'],p['y'],expected=interaction)
        if not found:
            continue
        s,cursor=found
        p=dict(x=cursor['x'],y=cursor['y'])
        before=vision.loot_messages(s.frame)
        reader=read_skin_receipts if interaction=='skin' else read_loot_receipts
        baseline=yield from ctx.work(reader,s.frame,folder,'before')
        # Slow OCR never authorizes a click. Reacquire and recheck the cursor.
        found=yield from move_pointer(ctx,vision,p['x'],p['y'],expected=interaction)
        if not found:
            return Result('failed','cursor_changed_before_click')
        s,_=found
        ctx.checkpoint['uncertain_transaction']=True
        yield from ctx.act(s,'click',(1,),interaction,.8,True)
        messages=skinnable=hands=0
        for _ in range(max(5,min(25,profile.get('loot_cursor_poll_frames',25)))):
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
        for n in range(6):
            # Loot text is delivered in separate item/coin updates after the cursor changes.
            yield from ctx.pause(.65)
            s=yield from fresh(ctx,.8,True)
            # Reading a new personal receipt sends no input. CTM can move the
            # camera/corpse after the verified click, so cursor position is not
            # a prerequisite for this independent effect check.
            receipts.append((yield from ctx.work(reader,s.frame,folder,f'after-{n}')))
            pair=receipts[-2:]
            confirmed=(len(pair)==2 and all(increased(baseline,r) for r in pair)) if interaction=='skin' else confirmed_pair(baseline,pair)
            if confirmed:break
        if confirmed:
            ctx.checkpoint['uncertain_transaction']=False
            return Result('completed','new_skin_material_ocr_two_frames' if interaction=='skin' else 'new_personal_loot_receipt_two_frames',
                          dict(interaction_confirmed=True,corpse_empty_verified=False),(str(folder),s.sequence))
        return Result('failed','no_new_loot_messages')
    return Result('skipped','no_verified_corpse')


def verify_unengaged(ctx):
    """Permit a new search only after three peaceful, undamaged observations."""
    for _ in range(3):
        s=yield from fresh(ctx,.95,True)
        if s.observation.target and s.observation.target_hp<.99:
            return Result('failed','target_already_damaged')
        yield from ctx.pause(.2)
    return Result('completed','unengaged_verified')


def roam(ctx, direction='right'):
    result=yield from recover(ctx)
    if result.status!='completed':
        return result
    s=yield from fresh(ctx,.95,True)
    if s.observation.target:
        yield from tap(ctx,41,80,'clear_target',.95,True)
        clear=0
        for _ in range(6):
            s=yield from fresh(ctx,.95,True)
            clear=clear+1 if not s.observation.target else 0
            if clear>=2:break
            yield from ctx.pause(.15)
        if clear<2:return Result('failed','roam_target_not_cleared')
    else:
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
