"""Adversarial acceptance accounting; all generated rows are fictional test data.

`complete_data` is also an executable schema example. Its simulated evidence must
never be copied to acceptance/evidence.json or represented as a real trial.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from runtime_acceptance import COMMON_CHECKS, LIVE_SCENARIOS, REQUIRED, SCENARIOS, ZERO_TOLERANCE, evaluate


def ledger(data,base):
    for i,row in enumerate(data['trials']):row['attempt_index']=i
    Path(base,'attempts.json').write_text(json.dumps(dict(complete=True,attempts=[
        {k:r[k] for k in ('id','kind','run_id','attempt_index')} for r in data['trials']])))


def complete_data(base):
    Path(base,'proof.json').write_text('{"fictional_test_evidence":true}')
    evidence=['proof.json']
    scope=dict(version='test-version',configuration_sha256='a'*64,character='test-character',
               map_id='test-map',calibration_version='test-calibration',evidence=evidence)
    checked=lambda name:dict(passed=True,mode='live' if name in LIVE_SCENARIOS else 'replay',evidence=evidence)
    data=dict(scope=scope,attempt_inventory='attempts.json',
              offline_tests=dict(run=99,failures=0,errors=0,skipped=0,evidence='proof.json'),
              checks=dict(common={k:checked(k) for k in COMMON_CHECKS}),
              measurements={k:dict(samples=[v],evidence=evidence) for k,v in
                            (('revocation_seconds',.09),('device_pulse_ms',500),('sent_frame_age_seconds',.49))},
              trials=[],holdout={})
    for stage,names in SCENARIOS.items():data['checks'][stage]={k:checked(k) for k in names}
    for stage,requirements in REQUIRED.items():
        for kind,n in requirements.items():
            if kind=='mineral_received':continue
            for i in range(n):
                row=dict(id=f'{kind}-{i}',kind=kind,passed=True,human_intervention=False,
                         mode='replay' if kind.endswith('_replay') else 'live',synthetic=False,
                         version=scope['version'],configuration_sha256=scope['configuration_sha256'],
                         initial_conditions={'labeled_fixture':'fictional unit test'},run_id='session',
                         evidence=evidence,trace_evidence=evidence,violations={k:0 for k in ZERO_TOLERANCE},facts={})
                if kind=='skin_round':row['facts']['hunt_round_id']=f'hunt_loot_round-{i}'
                if kind=='round_trip':row['facts']['endpoints']=[dict(error=.19,stationary_frames=2)]*2
                if kind=='mineral_attempt':row['facts']['mineral_received']=True
                if kind=='detour_resume':row['facts']=dict(total_seconds=89,returned_to_route=True,original_goal_resumed=True)
                if kind=='vendor_round_trip':
                    row['facts']=dict(empty_before=0,empty_after=1,returned_to_task=True,
                        sales=[dict(quality='poor',category='junk',recognized=True,evidence=evidence,
                                    quantity_before=1,quantity_after=0,coins_before=10,coins_after=11)])
                if kind=='cast':row['facts']=dict(bite_labeled=True,conditions_met=True,reel_count=1,bite_at=10,reel_sent_at=10.7)
                if kind=='failure_review':
                    row.update(mode='review',source_mode='historical_live',failure_kind=('navigation','recognition','unconfirmed_effect')[i])
                if kind=='improvement_cycle':
                    row['steps']={k:evidence for k in ('real_failure','candidate_change','old_failure_replay','negative_regression','controlled_live_recheck')}
                if kind in ('combined_run','fishing_run'):
                    row.update(duration_seconds=1800,coverage=['navigation','combat','mining','vendor','fishing','audio'])
                data['trials'].append(row)
    common=dict(development_sessions=['dev-session'],evaluation_sessions=['heldout-session'],
                evidence=evidence,synthetic=False,complete_segments=True,tp=30,fp=0,fn=0)
    data['holdout']['mining']=dict(common,positive_clips=30,negative_clips=30,latency_p95_seconds=1.9)
    data['holdout']['fishing']=dict(common,positive_events=30,negative_seconds=1800,false_reels=0,
                                   reel_latency_p95_seconds=.7,includes_capture_latency=True)
    ledger(data,base)
    return data


class AcceptanceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=self.temp.name;self.data=complete_data(self.base)

    def report(self):return evaluate(self.data,self.base)

    def test_complete_explicit_schema_can_pass_accounting_without_activation(self):
        result=self.report()
        self.assertTrue(result['all_passed'],result['stages'])
        self.assertNotIn('activate',result)

    def test_synthetic_or_unspecified_mode_cannot_count_as_live_acceptance(self):
        for changed in (dict(synthetic=True),dict(mode='replay'),dict(synthetic='false')):
            data=copy.deepcopy(self.data);data['trials'][0].update(changed)
            self.assertFalse(evaluate(data,self.base)['stages']['core']['passed'])

    def test_omitted_failed_attempt_and_reordered_ledger_fail(self):
        self.data['trials'].pop(0)
        self.assertFalse(self.report()['all_passed'])
        self.assertIn('complete_attempt_inventory_missing_or_mismatched',self.report()['stages']['core']['blockers'])

    def test_intervened_round_breaks_consecutive_streak(self):
        row=copy.deepcopy(self.data['trials'][0]);row.update(id='human-takeover',human_intervention=True)
        self.data['trials'].insert(2,row);ledger(self.data,self.base)
        result=self.report()['stages']['core']
        self.assertEqual(result['counts']['hunt_loot_round'],5)
        self.assertFalse(result['passed'])
        self.assertIn('five_consecutive_hunt_loot_rounds_required',result['blockers'])

    def test_missing_negative_or_boolean_safety_counts_never_mean_zero(self):
        for value in (None,-1,False,float('nan')):
            data=copy.deepcopy(self.data)
            data['trials'][0]['violations']['cancelled_input_sent']=value
            self.assertFalse(evaluate(data,self.base)['all_passed'])

    def test_one_critical_violation_blocks_every_stage(self):
        self.data['trials'][0]['violations']['protected_item_sold']=1
        result=self.report()
        self.assertTrue(all(not stage['passed'] for stage in result['stages'].values()))

    def test_nonfinite_or_negative_latency_and_duration_are_rejected(self):
        for value in (float('nan'),float('inf'),-.1,True):
            for metric in ('latency_p95_seconds','negative_clips'):
                data=copy.deepcopy(self.data);data['holdout']['mining'][metric]=value
                self.assertFalse(evaluate(data,self.base)['stages']['mining']['passed'])
            data=copy.deepcopy(self.data);data['holdout']['fishing']['reel_latency_p95_seconds']=value
            self.assertFalse(evaluate(data,self.base)['stages']['fishing']['passed'])

    def test_fishing_timely_rate_is_derived_from_all_eligible_casts(self):
        self.data['holdout']['fishing']['live_timely_reel_rate']=1
        casts=[r for r in self.data['trials'] if r['kind']=='cast']
        for row in casts[:4]:row['facts']['reel_sent_at']=11
        result=self.report()['stages']['fishing']
        self.assertAlmostEqual(result['measured_timely_reel_rate'],26/30)
        self.assertFalse(result['passed'])

    def test_duplicate_or_unlabeled_reel_fails_even_if_summary_is_zero(self):
        row=next(r for r in self.data['trials'] if r['kind']=='cast')
        row['facts']['reel_count']=2
        self.assertFalse(self.report()['stages']['fishing']['passed'])
        row['facts'].update(reel_count=1,bite_labeled=False)
        self.assertFalse(self.report()['stages']['fishing']['passed'])

    def test_adjacent_same_session_holdout_and_missing_scenario_fail(self):
        self.data['holdout']['mining']['evaluation_sessions']=['dev-session']
        self.assertFalse(self.report()['stages']['mining']['passed'])
        del self.data['checks']['vendor']['unknown_item']
        self.assertFalse(self.report()['stages']['vendor']['passed'])

    def test_endpoint_and_sale_require_observed_facts(self):
        trip=next(r for r in self.data['trials'] if r['kind']=='round_trip')
        trip['facts']['endpoints'][0]['stationary_frames']=1
        self.assertFalse(self.report()['stages']['navigation']['passed'])
        sale=next(r for r in self.data['trials'] if r['kind']=='vendor_round_trip')
        sale['facts']['sales'][0]['coins_after']=10
        self.assertFalse(self.report()['stages']['vendor']['passed'])

    def test_offline_boolean_counts_and_missing_scope_never_release(self):
        self.data['offline_tests']['failures']=False
        self.assertFalse(self.report()['offline_baseline_passed'])
        self.data['offline_tests']['failures']=0;self.data['scope']['map_id']='unconfirmed'
        self.assertFalse(self.report()['all_passed'])

    def test_review_needs_all_improvement_steps_and_measured_live_duration(self):
        cycle=next(r for r in self.data['trials'] if r['kind']=='improvement_cycle')
        del cycle['steps']['controlled_live_recheck']
        run=next(r for r in self.data['trials'] if r['kind']=='combined_run')
        run['duration_seconds']=float('nan')
        self.assertFalse(self.report()['stages']['review']['passed'])


if __name__=='__main__':unittest.main()
