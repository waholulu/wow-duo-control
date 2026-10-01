import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
import cv2
import numpy as np
from fishing_config import load_config, crop
from fishing_trial import match, receipts, main, read_valid_fishing_frame

ROOT=Path(__file__).resolve().parent

class FishingConfigurationTests(unittest.TestCase):
    def test_black_capture_wakes_once_then_requires_valid_frame(self):
        config={'frame_max_age':.5,'frame_size':[10,10],'player_roi':[0,0,2,2],'player_green_min':70}
        good=np.zeros((10,10,3),np.uint8);good[:2,:2,1]=140
        feed=Mock();feed.raw_frame.side_effect=[(np.zeros_like(good),.1),(good,.1)]
        box=Mock()
        with tempfile.TemporaryDirectory() as d,patch('fishing_trial.time.sleep'):
            folder=Path(d)
            self.assertTrue(np.array_equal(read_valid_fishing_frame(feed,config,box,10**12,folder/'STOP',folder/'faults.jsonl'),good))
            box.command.assert_called_once_with('km.move(50,20)')
            events=[json.loads(x)['event'] for x in (folder/'faults.jsonl').read_text().splitlines()]
            self.assertEqual(events,['black_capture_wake_sent','black_capture_recovered'])

    def test_nonblack_player_failure_never_sends_input(self):
        config={'frame_max_age':.5,'frame_size':[10,10],'player_roi':[0,0,2,2],'player_green_min':70}
        bad=np.full((10,10,3),20,np.uint8);feed=Mock();feed.raw_frame.return_value=(bad,.1);box=Mock()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            with self.assertRaisesRegex(RuntimeError,'Player frame unavailable'):
                read_valid_fishing_frame(feed,config,box,10**12,folder/'STOP',folder/'faults.jsonl')
            box.command.assert_not_called()
            self.assertEqual(feed.raw_frame.call_count,3)

    def test_persistent_black_capture_stops_after_one_wake(self):
        config={'frame_max_age':.5,'frame_size':[10,10],'player_roi':[0,0,2,2],'player_green_min':70}
        feed=Mock();feed.raw_frame.return_value=(np.zeros((10,10,3),np.uint8),.1);box=Mock();clock=[0]
        def tick():
            clock[0]+=.3
            return clock[0]
        with tempfile.TemporaryDirectory() as d,patch('fishing_trial.time.sleep'),patch('fishing_trial.time.monotonic',side_effect=tick):
            folder=Path(d)
            with self.assertRaisesRegex(RuntimeError,'Black capture persisted after wake'):
                read_valid_fishing_frame(feed,config,box,100,folder/'STOP',folder/'faults.jsonl')
            box.command.assert_called_once_with('km.move(50,20)')

    def test_invalid_config_rejected_before_devices(self):
        for change in ({'casts':0},{'max_seconds':float('nan')},{'cast_key':'enter'},
                       {'world_roi':[1900,1000,100,100]},{'ring_roi':[0,0,999,11]},
                       {'template_scales':[float('nan')]},{'target_streak':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):load_config(overrides=change)

    def test_check_config_opens_no_devices_and_applies_overrides(self):
        with patch('fishing_trial.Feed') as feed,patch('fishing_trial.KMBox') as box,patch('builtins.print'):
            self.assertEqual(main(['--check-config','--casts','3','--cast-key','7','--max-seconds','150']),0)
            feed.assert_not_called();box.assert_not_called()
        c=load_config(overrides={'casts':3,'cast_key':'7','max_seconds':150})
        self.assertEqual((c['casts'],c['cast_hid'],c['max_seconds']),(3,36,150))

    def test_saved_true_and_false_motion_replay(self):
        c=load_config()
        for run,cast,expected in [('155024','01',True),('155220','01',False),('155450','01',True),('155450','02',False)]:
            folder=ROOT/f'runs/fishing-live-20260928-{run}/cast-{cast}'
            images=[cv2.imread(str(f)) for f in sorted(folder.glob('roi-*.png'))]
            self.assertGreater(len(images),10)
            reference=crop(images[c['reference_frame']-1],c['ring_roi'])
            found=False
            for im in images[c['baseline_frames']:]:
                _,score,_,(_,y)=cv2.minMaxLoc(cv2.matchTemplate(crop(im,c['ring_search_roi']),reference,cv2.TM_CCOEFF_NORMED))
                found|=y+c['ring_search_roi'][1]-c['ring_roi'][1]>=c['drop_pixels'] and score>=c['ring_min']
            self.assertEqual(found,expected,(run,cast))

    def test_all_ten_saved_catches_keep_motion_trigger(self):
        c=load_config()
        folders=sorted((ROOT/'runs/fishing-live-20260928-160617').glob('cast-*'))+sorted((ROOT/'runs/fishing-live-20260928-160751').glob('cast-*'))
        self.assertEqual(len(folders),10)
        for folder in folders:
            events=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines()]
            point=next(e['candidate'] for e in events if e['event']=='bobber');x,y=point['x'],point['y']
            gear=next(e for e in events if e['event']=='cursor_mask');gx,gy=gear['x'],gear['y']
            px=x+c['patch_offset'][0];py=y+c['patch_offset'][1]
            mask=np.ones((c['patch_offset'][3],c['patch_offset'][2]),bool)
            mask[max(0,gy-10-py):min(mask.shape[0],gy+28-py),max(0,gx-10-px):min(mask.shape[1],gx+28-px)]=False
            images=[cv2.imread(str(f)) for f in sorted(folder.glob('roi-*.png'))]
            ring=crop(images[c['reference_frame']-1],c['ring_roi']);found=False
            for i in range(c['baseline_frames'],len(images)):
                im=images[i];delta=im.astype(float)-np.median(images[i-c['median_frames']:i],axis=0)
                submerged=int(crop((delta.mean(axis=2)<-c['dark_delta'])&mask,c['submerged_roi']).sum())
                _,score,_,(_,ry)=cv2.minMaxLoc(cv2.matchTemplate(crop(im,c['ring_search_roi']),ring,cv2.TM_CCOEFF_NORMED))
                found|=ry+c['ring_search_roi'][1]-c['ring_roi'][1]>=c['drop_pixels'] and score>=c['ring_min'] and submerged>=c['submerged_pixels']
            self.assertTrue(found,str(folder))

    def test_ocr_correction_on_saved_actual_receipt(self):
        path=ROOT/'runs/fishing-live-20260928-160617/cast-02/after-15.jpg'
        with tempfile.TemporaryDirectory() as directory:
            r=receipts(cv2.imread(str(path)),Path(directory),'replay')
        self.assertEqual(r['skill'],43);self.assertTrue(r['fish'])

    def test_failure_persists_terminal_status(self):
        with tempfile.TemporaryDirectory() as directory,patch('fishing_trial.run_session',side_effect=RuntimeError('device missing')):
            out=Path(directory)/'run'
            with self.assertRaisesRegex(RuntimeError,'device missing'):
                main(['--execute','--output',str(out)])
            self.assertEqual(json.loads((out/'status.json').read_text())['state'],'failed')

    def test_menu_default_saves_without_execution(self):
        import fishing_menu
        with tempfile.TemporaryDirectory() as directory,patch.object(fishing_menu,'ROOT',Path(directory)),patch('builtins.input',side_effect=['']*6),patch('builtins.print'),patch('fishing_menu.subprocess.call',return_value=0) as call:
            self.assertEqual(fishing_menu.main(),0)
            saved=json.loads((Path(directory)/'calibration/fishing_user.json').read_text())
            self.assertEqual(saved['casts'],10)
            self.assertIn('--check-config',call.call_args.args[0]);self.assertNotIn('--execute',call.call_args.args[0])

    def test_unified_dispatch(self):
        import wow_control
        with patch('fishing_trial.main',return_value=0) as run:
            wow_control.main(['--task','fish-trial','--check-config'])
            run.assert_called_once_with(['--check-config'])

if __name__=='__main__':unittest.main()
