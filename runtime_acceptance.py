"""Evidence-based acceptance accounting. Missing evidence never enables a gate.

Counts are descriptive (and retain legacy evidence); a passed gate additionally
requires explicit provenance, a complete attempt ledger, finite measurements,
and all safety/scenario checks. This tool does not certify truth of recordings or
activate capabilities: the referenced evidence remains reviewable source data.
"""
import argparse
import json
import math
from pathlib import Path
from runtime_store import atomic_json

REQUIRED={
    'core':{'hunt_loot_round':5,'skin_round':3,'mana_recovery':1},
    'navigation':{'round_trip':3,'defend_resume_live':1,'escape_resume_live':1,
                  'defend_resume_replay':3,'escape_resume_replay':3},
    'mining':{'mineral_attempt':5,'mineral_received':4,'detour_resume':3},
    'vendor':{'vendor_round_trip':3},
    'fishing':{'cast':30,'fishing_interruption':1},
    'review':{'failure_review':3,'improvement_cycle':1,'combined_run':1,'fishing_run':1}}
ZERO_TOLERANCE=('protected_item_sold','cancelled_input_sent','concurrent_input','unsupported_success','duplicate_reel')
SCENARIOS={
    'core':('old_frame','window_relocation','duplicate_events','ocr_delay','serial_exception',
            'phase_exception','stop_file','process_termination','explicit_terminal_states','mana_recovery_resume'),
    'navigation':('coordinate_anomaly','no_displacement','oscillation','route_obstructed',
                  'unknown_attacker','low_health','invalid_video','resume_current_position','preserve_goal'),
    'mining':('mineral_disappeared','unreachable','no_tool','insufficient_skill','bag_full',
              'under_attack','failed_candidate_not_retried','return_failure_stops','no_forbidden_detour'),
    'vendor':('protected_categories','unknown_item','wrong_merchant','window_closed',
              'rescan_after_sale','failed_sale_kill_only','unreliable_return_stops'),
    'fishing':('game_audio_source','unplug_no_microphone_fallback','reconnect_requires_sync',
               'visual_fallback_requires_acceptance','bobber_lost','wait_timeout','other_water_sounds','audio_disconnect'),
    'review':('failure_evidence_locatable','facts_separate_from_hypotheses','all_attempts_retained')}
COMMON_CHECKS=('baseline_assertions_preserved','cancelled_queue_not_replayed','exclusive_input',
               'success_evidence_chain','missing_recording_explicit','atomic_terminal_state')
LIVE_SCENARIOS={'game_audio_source','unplug_no_microphone_fallback','reconnect_requires_sync'}


def integer(value):
    return type(value) is int and value>=0


def number(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0


def evidence_exists(files,base):
    return (isinstance(files,list) and bool(files) and all(isinstance(p,str) and p
            and (base/p).is_file() and (base/p).stat().st_size>0 for p in files))


def evaluate(data, base):
    base=Path(base)
    invalid=[]
    trials=[]
    identities=set()
    raw=data.get('trials',[])
    if not isinstance(raw,list):
        raw=[]
        invalid.append('trials_must_be_list')
    for row in raw:
        if not isinstance(row,dict):
            invalid.append('trial_must_be_object')
            continue
        identity=row.get('id')
        if not isinstance(identity,str) or not identity or identity in identities:
            invalid.append('missing_or_duplicate_trial_id')
            continue
        identities.add(identity)
        if not isinstance(row.get('kind'),str):
            invalid.append('missing_or_invalid_trial_kind:'+identity)
            continue
        if not evidence_exists(row.get('evidence'),base):
            invalid.append('missing_evidence:'+identity)
            continue
        trials.append(row)  # Preserve intervened/failed attempts in sequence.
    unattended=[r for r in trials if r.get('human_intervention') is False]
    zero={name:0 for name in ZERO_TOLERANCE}
    violation_issues=[]
    for row in raw:
        if not isinstance(row,dict):continue
        values=row.get('violations',{})
        if not isinstance(values,dict):values={}
        for name in ZERO_TOLERANCE:
            value=values.get(name)
            if not integer(value):
                violation_issues.append(f"{row.get('id','unknown')}:{name}:unmeasured")
            else:zero[name]+=value
    tests=data.get('offline_tests',{})
    if not isinstance(tests,dict):tests={}
    baseline=(integer(tests.get('run')) and tests['run']>=99
              and all(integer(tests.get(k)) and tests[k]==0 for k in ('failures','errors','skipped'))
              and evidence_exists([tests.get('evidence')],base))
    scope=data.get('scope',{})
    if not isinstance(scope,dict):scope={}
    digest=scope.get('configuration_sha256')
    scope_valid=(isinstance(scope.get('version'),str) and bool(scope['version'])
                 and isinstance(digest,str) and len(digest)==64 and all(c in '0123456789abcdef' for c in digest)
                 and all(isinstance(scope.get(k),str) and scope[k] and scope[k]!='unconfirmed'
                         for k in ('character','map_id','calibration_version'))
                 and evidence_exists(scope.get('evidence'),base))
    # A ledger exported from the bounded runs is separate from selected trial
    # summaries. Requiring exact identities/order exposes omitted attempts.
    inventory_ok=False
    ledger_path=data.get('attempt_inventory')
    if isinstance(ledger_path,str) and evidence_exists([ledger_path],base):
        try:
            ledger=json.loads((base/ledger_path).read_text())
            inventory=ledger.get('attempts')
            wanted=[{k:r.get(k) for k in ('id','kind','run_id','attempt_index')} for r in raw if isinstance(r,dict)]
            inventory_ok=(ledger.get('complete') is True and isinstance(inventory,list)
                          and len(wanted)==len(raw) and inventory==wanted)
        except (OSError,ValueError,AttributeError):pass
    # Trial provenance is a release requirement, not a claim that an arbitrary
    # evidence file's existence proves the declared in-game result.
    def trial_issue(row):
        kind=row.get('kind')
        if type(row.get('passed')) is not bool or type(row.get('human_intervention')) is not bool:
            return 'explicit_outcome_and_intervention_required'
        expected='replay' if isinstance(kind,str) and kind.endswith('_replay') else 'live'
        if kind=='failure_review':expected='review'
        if row.get('mode')!=expected or row.get('synthetic') is not False:
            return 'real_provenance_required'
        if (row.get('version')!=scope.get('version') or row.get('configuration_sha256')!=digest
                or not isinstance(row.get('initial_conditions'),dict) or not row['initial_conditions']):
            return 'version_configuration_or_starting_conditions_missing'
        if not isinstance(row.get('run_id'),str) or not row['run_id'] or not integer(row.get('attempt_index')):
            return 'attempt_sequence_missing'
        if kind=='failure_review' and row.get('source_mode') not in ('live','historical_live'):
            return 'real_failure_source_required'
        if not evidence_exists(row.get('trace_evidence'),base):
            return 'observation_action_result_trace_missing'
        return None
    provenance={r['id']:trial_issue(r) for r in trials}
    sequence_seen=set()
    last_index={}
    for row in trials:
        run_id,index=row.get('run_id'),row.get('attempt_index')
        if not isinstance(run_id,str) or not integer(index):continue
        key=(run_id,index)
        if key in sequence_seen or index<=last_index.get(run_id,-1):
            provenance[row['id']]='duplicate_or_out_of_order_attempt'
        sequence_seen.add(key)
        last_index[run_id]=index
    def checked(item,live=False):
        return (isinstance(item,dict) and item.get('passed') is True
                and item.get('mode')==('live' if live else 'replay')
                and evidence_exists(item.get('evidence'),base))
    checks=data.get('checks',{})
    if not isinstance(checks,dict):checks={}
    common=checks.get('common',{})
    if not isinstance(common,dict):common={}
    common_missing=[name for name in COMMON_CHECKS if not checked(common.get(name))]
    measurements=data.get('measurements',{})
    if not isinstance(measurements,dict):measurements={}
    for name,maximum in (('revocation_seconds',.1),('device_pulse_ms',500),('sent_frame_age_seconds',.5)):
        metric=measurements.get(name,{})
        if not isinstance(metric,dict):metric={}
        values=metric.get('samples')
        if (not isinstance(values,list) or not values or not all(number(v) and v<=maximum for v in values)
                or not evidence_exists(metric.get('evidence'),base)):
            common_missing.append(name)
    stages={}
    for stage,requirements in REQUIRED.items():
        counts={kind:sum(r.get('kind')==kind and r.get('passed') is True for r in unattended) for kind in requirements}
        if stage=='mining':
            counts['mineral_attempt']=sum(r.get('kind')=='mineral_attempt' for r in unattended)
            counts['mineral_received']=sum(r.get('kind')=='mineral_attempt' and r.get('passed') is True
                and isinstance(r.get('facts'),dict) and r['facts'].get('mineral_received') is True for r in unattended)
        if stage=='fishing':counts['cast']=sum(r.get('kind')=='cast' for r in unattended)
        blockers=[]
        if not baseline:blockers.append('offline_baseline_incomplete')
        if invalid:blockers.append('invalid_evidence')
        if not scope_valid:blockers.append('acceptance_scope_missing')
        if not inventory_ok:blockers.append('complete_attempt_inventory_missing_or_mismatched')
        if violation_issues:blockers.append('zero_tolerance_measurements_missing_or_invalid')
        if any(zero.values()):blockers.append('zero_tolerance_violation')
        blockers.extend('common:'+name for name in common_missing)
        relevant=[r for r in trials if r.get('kind') in requirements]
        blockers.extend('trial:'+r['id']+':'+provenance[r['id']] for r in relevant if provenance[r['id']])
        blockers.extend('count:'+kind for kind,n in requirements.items() if counts[kind]<n)
        stage_checks=checks.get(stage,{})
        if not isinstance(stage_checks,dict):stage_checks={}
        blockers.extend('scenario:'+name for name in SCENARIOS[stage]
                        if not checked(stage_checks.get(name),name in LIVE_SCENARIOS))
        stages[stage]=dict(counts=counts,required=requirements,blockers=blockers,passed=False)
    def consecutive(kind,required,success=True):
        streak=[]
        run_id=None
        for row in trials:
            if row.get('kind')!=kind:continue
            if row.get('run_id')!=run_id:streak=[]
            run_id=row.get('run_id')
            ok=row.get('human_intervention') is False and (not success or row.get('passed') is True)
            streak=streak+[row['id']] if ok else []
            if len(streak)>=required:return set(streak[-required:])
        return set()
    core_streak=consecutive('hunt_loot_round',5)
    if not core_streak:stages['core']['blockers'].append('five_consecutive_hunt_loot_rounds_required')
    skins={r.get('facts',{}).get('hunt_round_id') for r in unattended if r.get('kind')=='skin_round'
           and r.get('passed') is True and isinstance(r.get('facts'),dict)
           and isinstance(r['facts'].get('hunt_round_id'),str)}
    if len(core_streak&skins)<3:stages['core']['blockers'].append('three_confirmed_skins_in_consecutive_rounds_required')
    if not consecutive('round_trip',3):stages['navigation']['blockers'].append('three_consecutive_round_trips_required')
    for row in unattended:
        if row.get('kind')=='round_trip' and row.get('passed') is True:
            endpoints=row.get('facts',{}).get('endpoints',[]) if isinstance(row.get('facts'),dict) else []
            if (not isinstance(endpoints,list) or len(endpoints)!=2 or any(not isinstance(p,dict)
                or not number(p.get('error')) or p['error']>.2 or not integer(p.get('stationary_frames'))
                or p['stationary_frames']<2 for p in endpoints)):
                stages['navigation']['blockers'].append('stationary_endpoint_evidence:'+row['id'])
    samples=data.get('holdout',{})
    if not isinstance(samples,dict):samples={}
    def sample_valid(sample,positive):
        if not isinstance(sample,dict):return False
        dev,hold=sample.get('development_sessions'),sample.get('evaluation_sessions')
        if (not isinstance(dev,list) or not isinstance(hold,list) or not dev or not hold
            or not all(isinstance(v,str) and v for v in dev+hold) or set(dev)&set(hold)
            or sample.get('synthetic') is not False or sample.get('complete_segments') is not True
            or not evidence_exists(sample.get('evidence'),base)):return False
        return (all(integer(sample.get(k)) for k in ('tp','fp','fn',positive))
                and sample['tp']+sample['fn']==sample[positive])
    def ratio(n,d):return n/d if d else 0
    mineral=samples.get('mining',{})
    mineral_ok=(sample_valid(mineral,'positive_clips') and mineral['positive_clips']>=30
        and integer(mineral.get('negative_clips')) and mineral['negative_clips']>=30
        and mineral['fp']<=mineral['negative_clips'] and ratio(mineral['tp'],mineral['tp']+mineral['fp'])>=.95
        and ratio(mineral['tp'],mineral['tp']+mineral['fn'])>=.9
        and number(mineral.get('latency_p95_seconds')) and mineral['latency_p95_seconds']<=2)
    if not mineral_ok:stages['mining']['blockers'].append('independent_mineral_detection_benchmark_required')
    for row in unattended:
        if row.get('kind')=='detour_resume' and row.get('passed') is True:
            facts=row.get('facts',{})
            if (not isinstance(facts,dict) or not number(facts.get('total_seconds')) or facts['total_seconds']>90
                or facts.get('returned_to_route') is not True or facts.get('original_goal_resumed') is not True):
                stages['mining']['blockers'].append('detour_return_evidence:'+row['id'])
    if not consecutive('vendor_round_trip',3):stages['vendor']['blockers'].append('three_consecutive_vendor_round_trips_required')
    for row in unattended:
        if row.get('kind')=='vendor_round_trip' and row.get('passed') is True:
            facts=row.get('facts',{})
            good=isinstance(facts,dict) and all(integer(facts.get(k)) for k in ('empty_before','empty_after'))
            good=good and facts['empty_after']>facts['empty_before'] and facts.get('returned_to_task') is True
            sales=facts.get('sales',[]) if isinstance(facts,dict) else []
            good=good and isinstance(sales,list) and bool(sales)
            if good:
                for sale in sales:
                    good=(isinstance(sale,dict) and sale.get('quality')=='poor' and sale.get('category')=='junk'
                        and sale.get('recognized') is True and evidence_exists(sale.get('evidence'),base)
                        and all(integer(sale.get(k)) for k in ('quantity_before','quantity_after','coins_before','coins_after'))
                        and sale['quantity_before']>sale['quantity_after'] and sale['coins_after']>sale['coins_before'])
                    if not good:break
            if not good:stages['vendor']['blockers'].append('sale_receipt_and_return_evidence:'+row['id'])
    sound=samples.get('fishing',{})
    sound_ok=(sample_valid(sound,'positive_events') and sound['positive_events']>=30
        and number(sound.get('negative_seconds')) and sound['negative_seconds']>=1800
        and ratio(sound['tp'],sound['tp']+sound['fn'])>=.95
        and integer(sound.get('false_reels')) and sound['false_reels']==0
        and number(sound.get('reel_latency_p95_seconds')) and sound['reel_latency_p95_seconds']<=.8
        and sound.get('includes_capture_latency') is True)
    if not sound_ok:stages['fishing']['blockers'].append('independent_audio_benchmark_required')
    casts=[r for r in unattended if r.get('kind')=='cast']
    eligible=timely=0
    for row in casts:
        facts=row.get('facts',{})
        if (not isinstance(facts,dict) or type(facts.get('bite_labeled')) is not bool
            or type(facts.get('conditions_met')) is not bool or not integer(facts.get('reel_count'))):
            stages['fishing']['blockers'].append('cast_measurements_missing:'+row['id'])
            continue
        if facts['reel_count']>1 or (facts['reel_count'] and not (facts['bite_labeled'] and facts['conditions_met'])):
            stages['fishing']['blockers'].append('duplicate_or_unsupported_reel:'+row['id'])
        if facts['bite_labeled'] and facts['conditions_met']:
            eligible+=1
            if (facts['reel_count']==1 and number(facts.get('bite_at')) and number(facts.get('reel_sent_at'))
                and 0<=facts['reel_sent_at']-facts['bite_at']<=.8):timely+=1
    stages['fishing']['measured_timely_reel_rate']=ratio(timely,eligible)
    if not consecutive('cast',30,False) or ratio(timely,eligible)<.9:
        stages['fishing']['blockers'].append('thirty_continuous_casts_and_measured_timely_rate_required')
    reviewed={r.get('failure_kind') for r in unattended if r.get('kind')=='failure_review' and r.get('passed') is True
              and isinstance(r.get('failure_kind'),str)}
    if not {'navigation','recognition','unconfirmed_effect'}<=reviewed:
        stages['review']['blockers'].append('three_distinct_real_failure_reviews_required')
    for kind,coverage in (('combined_run',{'navigation','combat','mining','vendor'}),('fishing_run',{'fishing','audio'})):
        found=False
        for row in unattended:
            modes=row.get('coverage',[])
            if (row.get('kind')==kind and row.get('passed') is True and number(row.get('duration_seconds'))
                and row['duration_seconds']>=1800 and isinstance(modes,list) and all(isinstance(v,str) for v in modes)
                and coverage<=set(modes)):found=True
        if not found:stages['review']['blockers'].append('bounded_live_coverage:'+kind)
    cycles=[r for r in unattended if r.get('kind')=='improvement_cycle' and r.get('passed') is True]
    cycle_steps=('real_failure','candidate_change','old_failure_replay','negative_regression','controlled_live_recheck')
    if not any(isinstance(r.get('steps'),dict) and all(evidence_exists(r['steps'].get(k),base) for k in cycle_steps) for r in cycles):
        stages['review']['blockers'].append('complete_improvement_evidence_chain_required')
    for details in stages.values():details['passed']=not details['blockers']
    return dict(schema_version=2,offline_baseline_passed=bool(baseline),invalid_evidence=invalid,
        unmeasured_violations=violation_issues,violations=zero,stages=stages,
        all_passed=all(s['passed'] for s in stages.values()),
        note='Counts alone do not establish acceptance. Source truth requires review; activation remains a reviewed change.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    result=evaluate(json.loads(a.input.read_text()),a.input.parent)
    atomic_json(a.output,result)
    print(json.dumps(result,ensure_ascii=False))
