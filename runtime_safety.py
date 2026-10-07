"""One owner for bounded threat response and terminal safety verification."""
from dataclasses import asdict
from runtime_types import GuardFailed, Result
from combat_exit_watch import ExitWatch
from threat_state import observe_threat


def needs_review(reason):
    return reason.startswith('combat_log_') or reason in (
        'target_lost_under_threat','unseen_target_under_threat','near_zero_target_unresolved',
        'target_changed_under_threat','invalid_or_stale_perception',
        'additional_attacker_suspected','damage_without_combat_indicator')


def resolve_threat(scheduler, deadline, *, interrupted=False):
    from runtime_skills import combat,recover
    if scheduler._stopped():return Result('cancelled','stop_requested',{'safety_exit':'user_stopped'})
    if getattr(scheduler.source,'death_review_pending',False):
        return Result('failed','death_review_required',{'safety_exit':'death_review_required'})
    # Interrupted work may defend only a previously established, fresh target.
    if interrupted:
        snapshot=scheduler.source.peek()
        if snapshot is not None and scheduler._may_defend(snapshot):
            result=scheduler.run('defend',lambda c:combat(c,scheduler,45),95,False,False,
                                 deadline=deadline,required_target=snapshot.target_track)
            scheduler.defense_kills=getattr(scheduler,'defense_kills',0)+result.facts.get('xp_events',0)
            if result.status=='cancelled':return result
            if result.status=='completed':
                recovered=scheduler.run('recover',recover,120,False,False,deadline=deadline)
                return Result(recovered.status,recovered.reason,
                              dict(recovered.facts,safety_exit='peace_confirmed' if recovered.status=='completed' else 'recovery_unresolved',
                                   manual_attention_required=recovered.status!='completed'),recovered.evidence)
        # Preserve prompt response to a fresh attack during a peaceful action.
        # The escape skill itself observes before every movement.
        return escape_and_recover(scheduler,deadline,True)
    watch=ExitWatch(scheduler.clock())
    action='observe'
    last_recorded_sequence=None
    calibration_lost_at=None
    while scheduler.clock()<deadline and not scheduler._stopped():
        if getattr(scheduler.source,'death_review_pending',False):
            action='death_review_required';break
        try:snapshot=scheduler.source.peek()
        except GuardFailed as exc:
            if str(exc)=='window_transform_invalidated':
                if calibration_lost_at is None:
                    calibration_lost_at=scheduler.clock()
                    scheduler.store.emit('safety_calibration_reacquiring')
                # Feed.frame may spend up to eight seconds reacquiring its
                # landmarks; observe without input through that same window.
                if scheduler.clock()-calibration_lost_at<8:
                    scheduler.stop_event.wait(.1)
                    continue
            scheduler.store.emit('safety_observation_unavailable',error=type(exc).__name__)
            action='observation_unavailable';break
        except Exception as exc:
            scheduler.store.emit('safety_observation_unavailable',error=type(exc).__name__)
            action='observation_unavailable';break
        calibration_lost_at=None
        if snapshot is not None:
            now=scheduler.clock();log=getattr(scheduler.source,'combat_log',None)
            recent=log.latest(now,watch.started-3,[]) if log else []
            threat=observe_threat(scheduler,snapshot,now)
            action=watch.step(snapshot.observation,snapshot.combat_known,now-snapshot.captured_at,now,
                sequence=snapshot.sequence,recent_incoming=any(e['kind']=='incoming_damage_text' for e in recent),
                threat_state=threat)
            if snapshot.sequence!=last_recorded_sequence:
                o=snapshot.observation
                scheduler.store.emit('safety_exit_sample',frame=snapshot.sequence,
                    captured_at=snapshot.captured_at,age=now-snapshot.captured_at,
                    valid=o.valid,combat_known=snapshot.combat_known,in_combat=o.in_combat,
                    player_hp=o.player_hp,target=o.target,
                    recent_log_kinds=[e['kind'] for e in recent],decision=action)
                last_recorded_sequence=snapshot.sequence
            if action=='peace_confirmed':break
            if action=='escape':return escape_and_recover(scheduler,deadline,False)
        scheduler.stop_event.wait(.1)
    if scheduler._stopped():action='user_stopped'
    return Result('completed' if action=='peace_confirmed' else 'failed',action,
                  {'safety_exit':action,'manual_attention_required':action!='peace_confirmed'})


def escape_and_recover(scheduler,deadline,heal):
    from runtime_skills import escape,recover
    if scheduler._stopped():return Result('cancelled','stop_requested',{'safety_exit':'user_stopped'})
    if getattr(scheduler.source,'death_review_pending',False):
        return Result('failed','death_review_required',{'safety_exit':'death_review_required'})
    escaped=scheduler.run('escape',escape,45,False,False,deadline=deadline)
    action='peace_confirmed' if escaped.status=='completed' else 'threat_unresolved'
    if heal and escaped.status=='completed':
        result=scheduler.run('recover',recover,120,False,False,deadline=deadline)
        if result.status!='completed':action='recovery_unresolved'
    else:result=escaped
    return Result(result.status,result.reason,dict(result.facts,safety_exit=action,
                  manual_attention_required=action!='peace_confirmed',escape_result=asdict(escaped)),result.evidence)
