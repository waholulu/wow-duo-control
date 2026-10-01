import json
import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from runtime_interactions import sale_allowed, money, personal_receipts, receipts_increased
from runtime_world import (MineralTracker, Places, Capabilities, load_profile,
                           profile_digest, RUNTIME_SOURCE_FILES, ROOT)
from runtime_audio import SoundDetector, features
from runtime_main import parser, validate, preflight


class FeatureTests(unittest.TestCase):
    def test_acceptance_digest_covers_every_runtime_behavior_component(self):
        required={
            'resource_readiness.py','timed_effects.py','melee_rotation.py',
            'threat_settlement.py','death_review.py','control_lock.py',
        }
        self.assertTrue(required.issubset(RUNTIME_SOURCE_FILES))
        profile=load_profile()
        baseline=profile_digest(profile)
        original=Path.read_bytes
        for name in required:
            with self.subTest(source=name):
                target=(ROOT/name).resolve()
                def changed(path, target=target):
                    data=original(path)
                    return data+b'\n# changed\n' if path.resolve()==target else data
                with patch.object(Path,'read_bytes',changed):
                    self.assertNotEqual(profile_digest(profile),baseline)

    def test_acceptance_source_list_covers_local_runtime_import_graph(self):
        modules={path.stem:path for path in ROOT.glob('*.py')}
        pending=[Path(name).stem for name in RUNTIME_SOURCE_FILES]
        imported=set()
        while pending:
            module=pending.pop()
            if module in imported or module not in modules:continue
            imported.add(module)
            tree=ast.parse(modules[module].read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    names=[item.name.split('.')[0] for item in node.names]
                elif isinstance(node,ast.ImportFrom) and node.module:
                    names=[node.module.split('.')[0]]
                else:
                    continue
                pending.extend(name for name in names
                               if name in modules and not name.startswith('test_'))
        listed={Path(name).stem for name in RUNTIME_SOURCE_FILES}
        self.assertEqual(imported-listed,set())

    def test_moving_minimap_tracks_keep_identity(self):
        tracker=MineralTracker()
        self.assertEqual(tracker.update([dict(x=1490,y=230)],0),[])
        self.assertEqual(tracker.update([dict(x=1498,y=233)],.5),[])
        points=tracker.update([dict(x=1506,y=236)],1)
        self.assertEqual(len(points),1)
        self.assertEqual(points[0]['identity'],'mineral-1')

    def test_candidate_gap_and_ambiguity_reset_confirmation(self):
        tracker=MineralTracker()
        for at in (0,.5,1):tracker.update([dict(x=1490,y=230)],at)
        self.assertEqual(tracker.update([dict(x=1490,y=230)],3),[])
        tracker=MineralTracker()
        tracker.update([dict(x=1480,y=230),dict(x=1490,y=230)],0)
        result=tracker.update([dict(x=1485,y=230)],.5)
        self.assertEqual(result,[])
        self.assertEqual(tracker.tracks[0]['frames'],1)

    def test_unknown_items_and_protected_categories_are_never_sold(self):
        tooltip={'items':[{'text':'断牙','confidence':.99}]}
        base=dict(name='断牙',quality='poor',category='junk',approved=True)
        self.assertTrue(sale_allowed(base,tooltip))
        for changed in (dict(category='equipment'),dict(category='material'),dict(category='quest'),dict(category='unknown'),dict(approved=False),dict(quality='common')):
            self.assertFalse(sale_allowed(dict(base,**changed),tooltip))
        for text in ('任务物品','需要等级5','护甲','材料'):
            protected={'items':tooltip['items']+[{'text':text,'confidence':.99}]}
            self.assertFalse(sale_allowed(base,protected))

    def test_money_requires_units_and_high_confidence(self):
        self.assertEqual(money({'items':[dict(text='1金2银3铜',confidence=.99)]}),10203)
        for text in ('10203','你拾取了3铜币','1金和3铜',''):
            self.assertIsNone(money({'items':[dict(text=text,confidence=.99)]}))
        self.assertIsNone(money({'items':[dict(text='1金币',confidence=.5)]}))

    def test_resource_receipt_requires_personal_prefix_and_new_occurrence(self):
        names=['铜矿石']
        self.assertEqual(personal_receipts({'items':[dict(text='铜矿石',confidence=1)]},names),{})
        self.assertEqual(personal_receipts({'items':[dict(text='[玩家]你获得了铜矿石',confidence=1)]},names),{})
        counts=personal_receipts({'items':[dict(text='你获得了[铜矿石]。',confidence=1)]},names)
        self.assertEqual(counts,{'铜矿石':1})
        self.assertTrue(receipts_increased({},counts))
        self.assertFalse(receipts_increased(counts,counts))

    def test_audio_template_match_is_deduplicated(self):
        rate=16000
        wave=np.sin(np.arange(8000)*2*np.pi*800/rate)*.1
        with patch('runtime_audio.load_sound',return_value=features(wave,rate)):
            detector=SoundDetector({'bite':{'file':'unused'}})
        self.assertEqual(len(detector.update(wave,rate,10,'session')),1)
        self.assertEqual(detector.update(wave,rate,10.1,'session'),[])
        self.assertEqual(detector.update(np.zeros(8000),rate,12.5,'session'),[])

    def test_route_requires_map_and_verified_points(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'places.json'
            data={'routes':{'route':{'map':'area','verified':True,'points':[[1,2],[3,4]]}}}
            p.write_text(json.dumps(data))
            places=Places(p)
            self.assertEqual(places.route('route','area'),[(1,2),(3,4)])
            with self.assertRaises(ValueError):places.route('route','other')
            places.data['routes']['route']['verified']=False
            with self.assertRaises(ValueError):places.route('route','area')

    def test_unvalidated_features_disabled_even_if_calibrated(self):
        profile={'fishing':{'calibrated':True}}
        with self.assertRaisesRegex(ValueError,'acceptance_required'):Capabilities(profile).require('fishing')
        self.assertTrue(Capabilities(profile,trial=True).require('fishing')['calibrated'])
        proof={'fishing':dict(offline_passed=True,live_passed=True,profile_sha256=profile_digest(profile))}
        self.assertTrue(Capabilities(profile,proof).require('fishing')['calibrated'])
        profile['fishing']['cast_key']=31
        with self.assertRaises(ValueError):Capabilities(profile,proof).require('fishing')

    def test_missing_calibration_never_bypassed_by_trial(self):
        with self.assertRaisesRegex(ValueError,'calibration_required'):
            Capabilities(load_profile(),trial=True).require('fishing')

    def test_cli_trials_are_bounded_and_no_input_is_default(self):
        p=parser()
        for flags in ([],['--execute','--trial'],['--execute','--target','nan','2']):
            with self.assertRaises(ValueError):validate(p.parse_args(['--output','unused',*flags]))
        cfg=validate(p.parse_args(['--task','observe','--max-seconds','1','--output','unused']))
        self.assertFalse(cfg.execute)

    def test_fishing_preflight_reports_missing_evidence(self):
        cfg=parser().parse_args(['--task','fish','--execute','--output','unused'])
        report=preflight(cfg,load_profile())
        self.assertFalse(report['ready'])
        self.assertTrue(any('fishing:' in issue for issue in report['issues']))


if __name__=='__main__':unittest.main()
