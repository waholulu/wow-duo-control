"""One scheduler, one input owner; cooperative skills never open hardware."""
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import math
import queue
import threading
import time
from runtime_types import Snapshot, Context, Observe, Wait, Work, Intent, Result, Cancelled, GuardFailed


class RecognitionWorkers:
    """Bounded workers whose abandoned results cannot prevent process exit.

    Native OCR work cannot be forcibly killed in a Python thread. Actual OCR
    subprocesses have their own timeout; close reports any worker exceeding its
    grace period instead of hanging forever or claiming it stopped.
    """
    def __init__(self, count=2):
        self.jobs = queue.Queue(maxsize=count)
        self.lock = threading.Lock()
        self.closed = False
        self.threads = [threading.Thread(target=self._run, name=f'recognition-{i}', daemon=True)
                        for i in range(count)]
        for thread in self.threads:
            thread.start()

    def _run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            future, function, args = job
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(function(*args))
            except BaseException as exc:
                future.set_exception(exc)

    def submit(self, function, *args):
        with self.lock:
            if self.closed:
                raise RuntimeError('Recognition workers are closed')
            future = Future()
            try:
                self.jobs.put_nowait((future, function, args))
            except queue.Full:
                raise RuntimeError('Recognition workers are saturated')
            return future

    def shutdown(self, timeout=1.0):
        with self.lock:
            if not self.closed:
                self.closed = True
                while True:
                    try:
                        job = self.jobs.get_nowait()
                    except queue.Empty:
                        break
                    if job is not None:
                        job[0].cancel()
                for _ in self.threads:
                    self.jobs.put(None)
        deadline = time.monotonic()+timeout
        for thread in self.threads:
            thread.join(max(0, deadline-time.monotonic()))
        return [thread.name for thread in self.threads if thread.is_alive()]


class Perception:
    def __init__(self, feed, vision, store, hz=8, monitor_minerals=False, route=None):
        self.feed, self.vision, self.store = feed, vision, store
        if not math.isfinite(hz) or not 0 < hz <= 30:
            raise ValueError('Capture rate must be in (0, 30]')
        self.period = 1/hz
        self.minerals_enabled = monitor_minerals
        self.route = route
        from combat_log_cv import CombatLogReader
        roi=vision.profile.get('combat_log_roi')
        self.combat_log=CombatLogReader(roi,store.emit,vision.profile.get('chat_tabs',{}).get('combat')) if roi else None

        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.latest = None
        self.error = None
        self.death_review_pending = False
        self.minerals = []
        self.worker = threading.Thread(target=self._run, name='perception', daemon=True)
        self.worker.start()

    def _run(self):
        from runtime_world import MineralTracker
        from mineral_monitor import mineral_candidates
        tracker = MineralTracker()
        next_mineral = 0
        sequence = 0
        from target_continuity import TargetContinuity
        target_tracker=TargetContinuity()
        last_invalid_reason = None
        try:
            while not self.stopping.is_set():
                started = time.monotonic()
                frame, elapsed = self.feed.frame()
                captured = time.monotonic()-elapsed
                vision_started=time.monotonic()
                observation = self.vision.observe(frame)
                if not observation.valid and observation.reason!=last_invalid_reason:
                    self.store.emit('evidence_request',reason=observation.reason,frame=sequence+1)
                last_invalid_reason=observation.reason if not observation.valid else None
                if observation.reason=='player_health_unreadable_or_zero':
                    callback=getattr(self.store,'death_review_callback',None)
                    if callback:
                        self.death_review_pending=True
                        callback(observation.reason)
                self.store.emit('perception_timing', capture_seconds=elapsed, recognition_seconds=time.monotonic()-vision_started)
                sequence += 1
                calibration = tuple(self.feed.calibration.transform or ())
                generation = self.feed.calibration.generation
                target_track=target_tracker.update(observation,getattr(self.vision,'target_label',None),
                                                   calibration,generation,captured)
                snapshot = Snapshot(sequence, captured, tuple(self.feed.calibration.transform or ()),
                    observation, frame, 'combat_roi' in self.vision.profile,
                    'casting' in self.vision.profile['bars'], generation,
                    target_track, getattr(self.vision,'target_label',None))
                with self.lock:
                    self.latest = snapshot
                self.store.frame(snapshot)
                if self.combat_log:self.combat_log.submit(frame,sequence,captured)
                if self.route:
                    self.route.submit(frame,captured)
                self.store.emit('observation', frame=sequence, captured_at=captured,
                    calibration=snapshot.calibration, observation=asdict(observation),
                    combat_known=snapshot.combat_known, casting_known=snapshot.casting_known,
                    calibration_generation=generation, target_track=snapshot.target_track)
                if self.minerals_enabled and started >= next_mineral:
                    points = tracker.update(mineral_candidates(frame), captured)
                    with self.lock:
                        self.minerals = points
                    for point in points:
                        if point['frames']==3:
                            self.store.emit('perception_event',event=dict(kind='mineral_candidate',at=captured,
                                identity=point['identity'],source='video',evidence=point))
                    next_mineral = started+.5
                # Keep combat and uncertain frames at the configured fast rate;
                # quiet out-of-combat observation needs only four frames/second.
                quiet=(observation.valid and not observation.target and not observation.in_combat
                       and observation.player_hp>=.95)
                period=max(self.period,.25) if quiet else self.period
                self.stopping.wait(max(0, period-(time.monotonic()-started)))
        except Exception as exc:
            self.error = exc
        finally:
            self.feed.close()
            if self.combat_log:self.combat_log.close()

    def peek(self):
        if self.stopping.is_set():
            raise GuardFailed('capture_stopped')
        if self.error:
            raise GuardFailed('capture_failed: '+str(self.error))
        with self.lock:
            if self.latest and (tuple(self.feed.calibration.transform or ())!=self.latest.calibration
                    or self.feed.calibration.generation != self.latest.calibration_generation):
                raise GuardFailed('window_transform_invalidated')
            return self.latest

    def close(self):
        self.stopping.set()
        self.worker.join(timeout=10)
        if self.worker.is_alive():
            raise RuntimeError('Capture worker did not stop')


class Executor:
    def __init__(self, box, source, record, clock=time.monotonic):
        self.box, self.source, self.record, self.clock = box, source, record, clock
        self.lock = threading.RLock()
        self.epoch = 0
        self.task = None
        self.closed = False
        self.ctm_pending = False
        self.ctm_enabled = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='input')

    def revoke(self, task=None):
        with self.lock:
            self.epoch += 1
            self.task = task
            if self.ctm_pending:
                self.pool.submit(self.cancel_ctm)
            return self.epoch

    def check(self, intent):
        now = self.clock()
        if getattr(self.source,'death_review_pending',False) and not intent.task.split('-',1)[-1].startswith(('escape','recover','observe')):
            raise GuardFailed('death_review_required')
        if self.closed or intent.epoch != self.epoch or self.task not in (None, intent.task):
            raise Cancelled('Input belongs to a cancelled task')
        if not math.isfinite(intent.expires_at) or not 0 <= intent.minimum_hp <= 1:
            raise GuardFailed('invalid_input_guard')
        if not 0 <= now-intent.snapshot.captured_at <= .5 or now >= intent.expires_at:
            raise GuardFailed('stale_frame_before_input')
        current = self.source.peek()
        now = self.clock()
        if (current is None or current.calibration != intent.snapshot.calibration
                or current.calibration_generation != intent.snapshot.calibration_generation):
            raise GuardFailed('calibration_changed_before_input')
        if not 0 <= now-current.captured_at <= .5 or not current.observation.valid:
            raise GuardFailed('unusable_live_view')
        if not 0 <= now-intent.snapshot.captured_at <= .5 or now >= intent.expires_at:
            raise GuardFailed('stale_frame_before_input')
        o = current.observation
        if not 0 <= o.player_hp <= 1 or not 0 <= o.player_mana <= 1:
            raise GuardFailed('invalid_player_state')
        if o.player_hp < intent.minimum_hp:
            raise GuardFailed('health_guard')
        if intent.peaceful and (not current.combat_known or o.in_combat):
            raise GuardFailed('combat_or_unknown')
        if intent.reason in ('attack','interact_target','face_target','approach_target','ranged_opener','cooldown_strike') and (
                not o.target or not o.target_allowed
                or not intent.snapshot.observation.target
                or not intent.snapshot.observation.target_allowed):
            raise GuardFailed('target_changed_before_input')
        if intent.reason in ('attack','interact_target','face_target','approach_target','ranged_opener','cooldown_strike') and (
                current.target_track != intent.snapshot.target_track):
            raise GuardFailed('target_changed_before_input')
        if intent.required_target is not None and current.target_track != intent.required_target:
            raise GuardFailed('defense_target_no_longer_associated')
        if intent.reason=='interact_target' and o.target_hp <= .02:
            raise GuardFailed('target_dead_before_interaction')
        if intent.reason in ('ranged_opener','cooldown_strike') and (o.target_hp<=.02 or o.opener_in_range is not True or o.casting):
            raise GuardFailed('opener_live_guard')
        if intent.reason in ('attack','interact_target') and o.casting:
            raise GuardFailed('already_casting')
        if intent.reason=='skip_unknown_without_attack' and (not current.combat_known or o.in_combat or o.player_hp<.95):
            raise GuardFailed('unknown_skip_guard_changed')
        if intent.reason=='skip_unseen_target' and (not current.combat_known or o.in_combat
                or o.player_hp<.95 or o.player_hp<intent.snapshot.observation.player_hp-.02):
            raise GuardFailed('unseen_skip_guard_changed')
        if not intent.snapshot.observation.valid:
            raise GuardFailed('invalid_decision_frame')
        # Read-only, nonblocking skill-specific evidence check (e.g. audio epoch).
        if intent.admission_guard is not None and intent.admission_guard() is not True:
            raise GuardFailed('skill_evidence_invalidated_before_input')
        if not 0 <= self.clock()-intent.snapshot.captured_at <= .5 or self.clock() >= intent.expires_at:
            raise GuardFailed('stale_frame_before_input')

    @contextmanager
    def admission(self, intent):
        with self.lock:
            self.check(intent)
            yield

    def submit(self, intent):
        self.record('action_proposed', task=intent.task, epoch=intent.epoch,
            action=intent.kind, values=intent.values, reason=intent.reason,
            frame=intent.snapshot.sequence)
        return self.pool.submit(self._send, intent)

    def _send(self, intent):
        sent = False
        wrote = False
        def written(count, complete):
            nonlocal sent, wrote
            wrote = True
            if intent.reason == 'interact_target' or (self.ctm_enabled and intent.reason=='loot' and intent.kind=='click'):
                self.ctm_pending = True
            elif complete and intent.reason in ('cancel_click_to_move','cancel_movement_after_kill'):
                self.ctm_pending = False
            if complete:
                sent = True
                self.record('action_sent', task=intent.task, epoch=intent.epoch,
                    action=intent.kind, values=intent.values, reason=intent.reason,
                    frame=intent.snapshot.sequence)
            else:
                self.record('action_write_partial', task=intent.task, epoch=intent.epoch,
                    action=intent.kind, reason=intent.reason, bytes_written=count)
        @contextmanager
        def gate():
            with self.admission(intent):
                yield written
        try:
            self.check(intent)
            if intent.kind == 'tap':
                key, duration = intent.values
                if (type(key) is not int or type(duration) is not int
                        or not 4 <= key <= 231 or not 20 <= duration <= 500):
                    raise ValueError('Key must be 4..231; duration 20..500 ms')
            elif intent.kind == 'move':
                dx, dy = intent.values
                if not all(math.isfinite(v) and abs(v) <= 60 for v in (dx,dy)):
                    raise ValueError('Logical mouse step exceeds 60 pixels')
                transform = intent.snapshot.calibration
                if len(transform) not in (3,4) or not all(math.isfinite(v) for v in transform):
                    raise GuardFailed('invalid_mouse_calibration')
                sx = transform[0]
                sy = transform[1] if len(transform)==4 else sx
                if sx <= 0 or sy <= 0:
                    raise GuardFailed('invalid_mouse_calibration')
            elif intent.kind == 'move_to':
                x,y,width,height=intent.values
                if (not all(type(v) is int for v in intent.values)
                        or not 320<=width<=8192 or not 320<=height<=8192
                        or not 0<=x<width or not 0<=y<height):
                    raise ValueError('Absolute mouse destination is invalid')
            elif intent.kind != 'click' or not (intent.values == (1,) or (intent.values == (0,) and intent.reason in ('select_chat_general','select_chat_combat') and intent.peaceful)):
                raise ValueError('Unsupported input intent')
            if self.box is None:
                self.record('action_simulated', task=intent.task, reason=intent.reason)
                return dict(sent=False, simulated=True)
            if intent.kind == 'tap':
                response = self.box.tap(key, duration, admission=gate)
            elif intent.kind == 'move':
                def pixels(delta, scale):
                    return 0 if delta == 0 else int(math.copysign(max(1, round(abs(delta)*scale)), delta))
                response = self.box.command(f'km.move({pixels(dx,sx)},{pixels(dy,sy)})', admission=gate)
            elif intent.kind == 'move_to':
                response = self.box.command(
                    f'km.Screen({width},{height});km.zero(1);km.moveto({x},{y});km.zero(0)',
                    admission=gate)
            elif intent.kind == 'click':
                response = self.box.command(f'km.click({intent.values[0]})', admission=gate)
            else:
                raise ValueError('Unsupported input intent')
            if not sent:
                raise RuntimeError('Transport did not confirm a complete command write')
            self.record('action_acknowledged', task=intent.task, reason=intent.reason,
                frame=intent.snapshot.sequence)
            return dict(sent=True, acknowledged=True)
        except Exception as exc:
            self.record('action_failed' if wrote else 'action_rejected', task=intent.task, reason=str(exc))
            if wrote and isinstance(exc,GuardFailed):raise GuardFailed('input_guard_failed_after_write') from exc
            raise

    def cancel_ctm(self):
        # Cancellation must remain possible after stale frames or revocation.
        # Serialized with input admission; this is only a 20 ms stop movement.
        with self.lock:
            if self.ctm_pending and self.box:
                self.box.tap(22,20)
                self.ctm_pending = False
                self.record('click_to_move_cancelled', reason='task_revoked_or_closed')

    def close(self):
        with self.lock:
            self.closed = True
            self.epoch += 1
        self.pool.shutdown(wait=True, cancel_futures=True)
        if self.box:
            try:
                self.cancel_ctm()
            finally:
                self.box.close()


class Scheduler:
    def __init__(self, source, executor, store, stop_event=None, clock=time.monotonic):
        self.source, self.executor, self.store, self.clock = source, executor, store, clock
        self.stop_event = stop_event or threading.Event()
        self.workers = RecognitionWorkers()
        self.task_number = 0
        self.last_hp = None
        self.last_sequence = 0
        self.last_threat = False
        from threat_state import ThreatMonitor
        self.threat_monitor = ThreatMonitor()
        self.engagement = None
        self.audio_events = []
        self.audio = None
        self.audio_seen = set()

    def stop(self):
        self.stop_event.set()
        self.executor.revoke()

    def _stopped(self):
        return self.stop_event.is_set() or (self.store.folder/'STOP').exists()

    def _threat(self, snapshot):
        from threat_state import observe_threat
        state = observe_threat(self, snapshot)
        self.last_sequence = snapshot.sequence
        o = snapshot.observation
        self.last_hp = o.player_hp
        self.last_threat = state.fresh and state.visual
        if self.engagement and (snapshot.target_track != self.engagement[0]
                or not snapshot.combat_known or not o.in_combat):
            self.engagement = None
        return self.last_threat

    def note_engagement(self, snapshot):
        """Record damage-associated observable target continuity, never species alone."""
        o = snapshot.observation
        if (snapshot.target_track is not None and snapshot.combat_known
                and o.valid and o.in_combat and o.target and o.target_allowed
                and 0 <= self.clock()-snapshot.captured_at <= .5):
            self.engagement = (snapshot.target_track, self.clock()+15,
                               snapshot.calibration, snapshot.calibration_generation)

    def _may_defend(self, snapshot):
        from threat_state import observe_threat
        threat = observe_threat(self, snapshot)
        o = snapshot.observation
        return bool(self.engagement and snapshot.target_track == self.engagement[0]
                    and threat.fresh and not threat.additional_suspected
                    and self.clock() <= self.engagement[1]
                    and snapshot.calibration == self.engagement[2]
                    and snapshot.calibration_generation == self.engagement[3]
                    and 0 <= self.clock()-snapshot.captured_at <= .5
                    and snapshot.combat_known and o.valid and o.in_combat
                    and o.target and o.target_allowed and o.player_hp >= .6
                    and o.player_mana >= .25)

    def _check_active(self, ctx):
        if getattr(self.source,'death_review_pending',False) and not ctx.task.split('-',1)[-1].startswith(('escape','recover','observe')):
            raise GuardFailed('death_review_required')
        if self._stopped():
            ctx.cancelled = True
            raise Cancelled('stop_requested')
        if self.clock() >= ctx.deadline:
            raise GuardFailed('skill_deadline')
        if self.store.error:
            raise GuardFailed('evidence_writer_failed: '+self.store.error)

    def _drive(self, factory, ctx, watch_threat):
        generator = factory(ctx)
        operation = None
        future = None
        wake = 0
        entered = self.clock()
        status_text_wait_started = None
        value = None
        try:
            self._check_active(ctx)
            operation = next(generator)
            while True:
                now = self.clock()
                self._check_active(ctx)
                snapshot = self.source.peek()
                if self.audio:
                    for event in self.audio.since(now-.5,'hurt'):
                        if event.identity not in self.audio_seen:
                            self.audio_seen.add(event.identity)
                            self.store.emit('threat_recheck_requested',event=event.identity)
                            # The continuously refreshed visual state below
                            # confirms combat; sound never selects a target.
                    if len(self.audio_seen)>100:
                        self.audio_seen={e.identity for e in self.audio.since(now-5,'hurt')}
                if snapshot is not None:
                    age=self.clock()-snapshot.captured_at
                    if (snapshot.observation.reason=='status_text_unreadable'
                            and snapshot.observation.player_hp>=.55 and 0<=age<=.5):
                        if status_text_wait_started is None:status_text_wait_started=now
                        if now-status_text_wait_started<1.5:
                            # Fresh frames continue; dispatch no input while text
                            # settles after a resource change. Never reuse old mana.
                            self.stop_event.wait(.05)
                            continue
                    else:
                        status_text_wait_started=None
                    if (snapshot.observation.valid and snapshot.observation.player_hp>=.85
                            and .5<age<=1 and isinstance(operation,(Observe,Wait,Work))):
                        # A healthy ongoing melee need not be cancelled for one
                        # delayed capture. This branch dispatches no input.
                        self.stop_event.wait(.03)
                        continue
                    if (snapshot.observation.valid and .5 < age <= 2.0
                            and (isinstance(operation,(Work,Observe,Wait)) or
                                 (isinstance(operation,Intent) and future is not None))
                            and not self.executor.ctm_pending):
                        # Await a fresh view during read-only recognition; no input
                        # is dispatched and the executor's .5 s guard is unchanged.
                        # Two seconds covers bounded OBS startup jitter while a
                        # persistent outage still fails closed.
                        self.stop_event.wait(.05)
                        continue
                    if not snapshot.observation.valid or not 0 <= age <= .5:
                        raise GuardFailed('invalid_or_stale_perception')
                    if watch_threat and not snapshot.combat_known:
                        raise GuardFailed('combat_status_unknown')
                    threat=self._threat(snapshot)
                    if watch_threat and threat:
                        return Result('failed','interrupted_by_threat', {'resumable':True})
                elif now-entered > 10:
                    raise GuardFailed('no_initial_observation')
                ready = False
                if isinstance(operation, Observe):
                    ready = snapshot is not None and snapshot.sequence > operation.after
                    value = snapshot
                elif isinstance(operation, Wait):
                    if not math.isfinite(operation.seconds) or operation.seconds < 0:
                        raise GuardFailed('invalid_wait_duration')
                    if not wake:
                        wake = now+operation.seconds
                    ready = now >= wake
                    value = None
                elif isinstance(operation, Work):
                    if not math.isfinite(operation.timeout) or operation.timeout <= 0:
                        raise GuardFailed('invalid_recognition_timeout')
                    if future is None:
                        future = self.workers.submit(operation.function, *operation.args)
                        wake = now+operation.timeout
                    if now > wake:
                        raise GuardFailed('recognition_timeout')
                    if future.done():
                        value = future.result()
                        ready = True
                elif isinstance(operation, Intent):
                    if future is None:
                        future = self.executor.submit(operation)
                    if future.done():
                        try:value = future.result()
                        except GuardFailed as exc:
                            # Deliver a rejected (unsent) action to the skill so
                            # it can recompute from a new frame, never replay it.
                            if str(exc) not in ('stale_frame_before_input','unusable_live_view'):raise
                            future,wake=None,0
                            operation=generator.throw(exc)
                            continue
                        ready = True
                else:
                    raise TypeError('Unknown skill operation')
                if ready:
                    self._check_active(ctx)
                    future, wake = None, 0
                    operation = generator.send(value)
                else:
                    self.stop_event.wait(.05)
        except StopIteration as done:
            self._check_active(ctx)
            if not isinstance(done.value, Result):
                raise TypeError('Skills must return a Result')
            return done.value
        finally:
            self.executor.revoke()
            if future:
                future.cancel()
            generator.close()

    def run(self, name, factory, seconds=90, interruptible=True, resume=True,
            *, deadline=None, required_target=None):
        skill_started_at=self.clock()
        self.task_number += 1
        task = f'{self.task_number:04d}-{name}'
        task_deadline = self.clock()+seconds
        if deadline is not None:
            task_deadline = min(task_deadline, deadline)
        ctx = Context(task, task_deadline, epoch=self.executor.revoke(task),
            clock=self.clock, record=self.store.emit, required_target=required_target,
            current_snapshot=self.source.peek)
        ctx.vision_profile=getattr(getattr(self.source,'vision',None),'profile',{})
        ctx.threat_monitor=self.threat_monitor
        ctx.threat_log=getattr(self.source,'combat_log',None)
        ctx.recovery_options=getattr(self,'recovery_options',{})
        ctx.corpse_hint=getattr(self,'corpse_hint',None)
        self.store.emit('skill_started', task=task, deadline=ctx.deadline)
        try:
            if not math.isfinite(seconds) or seconds <= 0 or not math.isfinite(task_deadline):
                raise GuardFailed('invalid_skill_deadline')
            permission=getattr(self,'combat_permission',None)
            if permission:
                issues=permission(name)
                if issues:raise GuardFailed('; '.join(issues))
            while True:
                result = self._drive(factory, ctx, interruptible)
                if result.reason != 'interrupted_by_threat':
                    break
                self._check_active(ctx)
                self.store.clip('threat')
                from runtime_safety import resolve_threat
                recovery=resolve_threat(self,ctx.deadline,interrupted=True)
                if recovery.status=='cancelled':
                    result=recovery
                    break
                if recovery.status!='completed':
                    result=Result('failed',recovery.reason,dict(recovery.facts,recovery=asdict(recovery)),recovery.evidence)
                    break
                self._check_active(ctx)
                if recovery.status == 'completed' and resume and not ctx.checkpoint.get('uncertain_transaction'):
                    ctx.epoch = self.executor.revoke(task)
                    ctx.sequence = 0
                    self.store.emit('skill_resumed', task=task, checkpoint=ctx.checkpoint)
                else:
                    result = Result('failed','recovery_failed_or_resume_unsafe', dict(recovery.facts,recovery=asdict(recovery)))
                    break
        except Cancelled as exc:
            ctx.cancelled = True
            result = Result('cancelled', str(exc),dict(ctx.checkpoint.get('confirmed_facts',{})))
        except Exception as exc:
            result = Result('failed', str(exc),dict(ctx.checkpoint.get('confirmed_facts',{})))
        self.executor.revoke()
        self.store.emit('skill_finished', task=task, result=asdict(result),
                        elapsed_seconds=max(0,self.clock()-skill_started_at))
        if result.status == 'completed':
            self.store.emit('effect_confirmed', task=task, facts=result.facts, evidence=result.evidence)
            self.store.clip('confirmed:'+name)
        else:
            self.store.clip(result.reason)
        return result

    def close(self):
        self.stop()
        unfinished = self.workers.shutdown()
        if unfinished:
            self.store.emit('recognition_shutdown_incomplete', workers=unfinished)
            raise RuntimeError('Recognition workers did not stop: '+', '.join(unfinished))
