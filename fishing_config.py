"""Data-only configuration for the scene-calibrated fishing trial."""
import argparse
import json
import math
from pathlib import Path
import cv2

ROOT = Path(__file__).resolve().parent
DEFAULT = ROOT / 'calibration/fishing_trial.json'


def crop(image, roi):
    x, y, w, h = roi
    return image[y:y+h, x:x+w]


def load_config(path=DEFAULT, overrides=None):
    c = json.loads(Path(path).read_text())
    c.setdefault('stop_mode','streak')
    c.setdefault('evidence_stride',1)
    c.setdefault('reel_delay_ms_range',[0,0])
    c.setdefault('cycle_delay_ms_range',[0,0])
    c.update({k: v for k, v in (overrides or {}).items() if v is not None})
    if c.get('schema_version') != 1:
        raise ValueError('不支持的配置版本')
    for key in ('reel_delay_ms_range','cycle_delay_ms_range'):
        value=c[key]
        if (not isinstance(value,list) or len(value)!=2 or
                any(type(x) is not int for x in value) or not 0<=value[0]<=value[1]<=5000):
            raise ValueError(f'{key}: 需要 0–5000 毫秒内的 [最小值,最大值]')
    for key, value in c.items():
        if isinstance(value, bool):
            raise ValueError(f'{key}: 不接受布尔值')
        if isinstance(value, (int, float)) and (not math.isfinite(value) or value <= 0):
            raise ValueError(f'{key}: 必须是有限正数')
    for key in ('casts','target_streak','key_duration_ms','position_attempts','position_tolerance',
                'alignment_attempts','baseline_frames','reference_frame','median_frames',
                'receipt_frames','cursor_step_limit','drop_pixels','submerged_pixels'):
        if type(c[key]) is not int:
            raise ValueError(f'{key}: 必须是整数')
    if c['stop_mode'] not in ('streak','bag_full'):
        raise ValueError('stop_mode 必须为 streak 或 bag_full')
    maximum_casts,maximum_seconds=(2000,43200) if c['stop_mode']=='bag_full' else (20,1000)
    if not 1 <= c['casts'] <= maximum_casts or not 1 <= c['target_streak'] <= 20 or not 1 <= c['max_seconds'] <= maximum_seconds:
        raise ValueError('次数/连续目标/总时限超出模式上限')
    if type(c['evidence_stride']) is not int or c['evidence_stride']<1:
        raise ValueError('evidence_stride 必须为正整数')
    if c['stop_mode']=='bag_full':
        layout=(ROOT/c['bag_layout']).resolve()
        if not layout.is_relative_to(ROOT) or not layout.is_file():
            raise ValueError('需要可读的合并背包标定')
        if type(c['bag_hid']) is not int or not 4<=c['bag_hid']<=231:
            raise ValueError('bag_hid 无效')
    if not 20 <= c['key_duration_ms'] <= 500:
        raise ValueError('按键时长限 20–500 毫秒')
    if str(c['cast_key']) not in '1234567890' or len(str(c['cast_key'])) != 1:
        raise ValueError('cast_key 必须是 0–9 中的一个按键')
    c['cast_key'] = str(c['cast_key'])
    c['cast_hid'] = 39 if c['cast_key'] == '0' else 29 + int(c['cast_key'])
    for key in ('candidate_min','old_candidate_max','new_candidate_margin','tooltip_min','gear_min','ring_min','ocr_confidence'):
        if not 0 < c[key] <= 1:
            raise ValueError(f'{key}: 范围应为 (0,1]')
    for key in ('serial_port','obs_source','name'):
        if not isinstance(c[key], str) or not c[key].strip():
            raise ValueError(f'{key}: 不能为空')
    if c['frame_size'] != [1920,1080]:
        raise ValueError('当前模板仅支持 1920×1080 采集；其他尺寸需要重新标定')
    def roi(key, bounds):
        v=c[key]
        if (not isinstance(v,list) or len(v)!=4 or any(type(x) is not int for x in v)
                or min(v[:2])<0 or min(v[2:])<=0 or v[0]+v[2]>bounds[0] or v[1]+v[3]>bounds[1]):
            raise ValueError(f'{key}: ROI 必须为边界内的 [x,y,宽,高]')
    for key in ('world_roi','cursor_roi','tooltip_roi','receipt_roi','player_roi'):
        roi(key,c['frame_size'])
    p=c['patch_offset']
    if not isinstance(p,list) or len(p)!=4 or any(type(v) is not int for v in p) or min(p[2:])<=0:
        raise ValueError('patch_offset 无效')
    # The full local patch must fit for any proposed world point.
    x,y,w,h=c['world_roi'];fw,fh=c['frame_size']
    if x+p[0]<0 or y+p[1]<0 or x+w+p[0]+p[2]>fw or y+h+p[1]+p[3]>fh:
        raise ValueError('水面范围超出局部检测补丁的有效边界')
    for key in ('ring_roi','ring_search_roi','submerged_roi'):
        roi(key,p[2:])
    if 'splash_roi' in c:roi('splash_roi',p[2:])
    for key,limit in (('candidate_peaks',20),('hover_candidate_limit',12),('hover_accept_radius',20)):
        if key in c and (type(c[key]) is not int or not 1<=c[key]<=limit):
            raise ValueError(f'{key}: 超出有界扫描范围')
    for key in ('hover_scan','clear_pointer_for_watch','reject_ambiguous_candidates'):
        if key in c and c[key] != 1:raise ValueError(f'{key}: 启用值必须为 1')
    if c.get('clear_pointer_for_watch'):
        value=c.get('watch_cursor_delta')
        if not c.get('hover_scan') or not isinstance(value,list) or len(value)!=2 or any(type(v) is not int or abs(v)>100 for v in value):
            raise ValueError('移开光标观察需要悬停核验及有效的有限位移')
    if any(c['ring_roi'][i]>c['ring_search_roi'][i] for i in (2,3)):
        raise ValueError('小环模板大于搜索区')
    if not 1 <= c['reference_frame'] <= c['baseline_frames'] or not 1 <= c['median_frames'] <= c['baseline_frames']:
        raise ValueError('参考帧/中值窗口超出基线帧数')
    if not 0 < c['cast_settle_seconds'] < c['wait_seconds'] <= 60 or c['minimum_bite_seconds'] >= c['wait_seconds']:
        raise ValueError('等待时序无效')
    for key in ('cursor_clear_delta','cursor_target_offset','gear_hotspot_offset'):
        if not isinstance(c[key],list) or len(c[key])!=2 or any(type(v) is not int or abs(v)>200 for v in c[key]):
            raise ValueError(f'{key}: 需要两个 -200..200 的整数')
    if not 1 <= c['position_attempts'] <= 30 or not 1 <= c['alignment_attempts'] <= 10 or not 1 <= c['cursor_step_limit'] <= 200:
        raise ValueError('鼠标迭代/步长超出试验范围')
    if not isinstance(c['template_scales'],list) or not c['template_scales'] or any(type(v) not in (int,float) or not math.isfinite(v) or not .25<=v<=2 for v in c['template_scales']):
        raise ValueError('template_scales 无效')
    if not isinstance(c['bobber_templates'],list) or not c['bobber_templates']:
        raise ValueError('缺少鱼漂模板')
    for name in c['bobber_templates']+[c['tooltip_template'],c['gear_template']]:
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or cv2.imread(str(path)) is None:
            raise ValueError(f'模板不可读: {name}')
    return c


def parse_options(argv=None):
    p=argparse.ArgumentParser(description='当前场景钓鱼：参数配置、只读检查、有界执行')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--config',type=Path,default=DEFAULT)
    p.add_argument('--casts',type=int)
    p.add_argument('--target-streak',type=int)
    p.add_argument('--max-seconds',type=float)
    p.add_argument('--cast-key')
    p.add_argument('--serial-port')
    p.add_argument('--output',type=Path)
    p.add_argument('--check-config',action='store_true',help='只校验配置/模板，不连接 OBS 或 KMBox')
    p.add_argument('--until-full',action='store_true',help='使用合并背包标定，直到全部格子占用')
    args=p.parse_args(argv)
    overrides={k:getattr(args,k) for k in ('casts','target_streak','max_seconds','cast_key','serial_port')}
    if args.until_full:overrides['stop_mode']='bag_full'
    c=load_config(args.config,overrides)
    return args,c
