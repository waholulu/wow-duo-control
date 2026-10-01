"""Chinese terminal menu; writes data settings and calls the same CLI."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from fishing_config import DEFAULT, ROOT, load_config


def main(argv=()):
    parser=argparse.ArgumentParser();parser.add_argument('--preset',choices=['lake','crowded'],default='lake');args=parser.parse_args(argv)
    default=ROOT/'calibration/fishing_crowded.json' if args.preset=='crowded' else DEFAULT
    path=ROOT/('calibration/fishing_crowded_user.json' if args.preset=='crowded' else 'calibration/fishing_user.json')
    source=path if path.exists() else default
    c=load_config(source)
    print('钓鱼参数设置（直接回车保留当前值；取消请按 Ctrl+C）')
    print('当前配置：'+c['name'])
    print('本地脚本独立执行，无需GPT；换地图/视角/浮漂外观需重新验证。')
    if c['stop_mode']=='bag_full':print('此模式按投竿/时限/满包停止；连续成功目标不作为停止条件。音频仅记录。')
    for key,label,convert in [('casts','最多投竿次数（1–20）',int),
                              ('target_streak','连续成功目标（1–20）',int),
                              ('max_seconds','最长运行秒数（1–1000）',float),
                              ('cast_key','钓鱼快捷键（0–9）',str),
                              ('serial_port','KM Box 串口',str)]:
        value=input(f'{label} [{c[key]}]：').strip()
        if value:c[key]=convert(value)
    c.pop('cast_hid',None)
    # Validate before replacing saved settings, without opening devices.
    load_config(default,c)
    action=input('1 只保存并检查（默认） / 2 保存并开始钓鱼：').strip()
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n')
    command=[sys.executable,str(ROOT/'wow_control.py'),'--task','fish-trial','--config',str(path)]
    command+=['--execute'] if action=='2' else ['--check-config']
    return subprocess.call(command,cwd=ROOT)


if __name__=='__main__':
    try:raise SystemExit(main(sys.argv[1:]))
    except (ValueError,KeyboardInterrupt) as e:print('\n未启动钓鱼：',e);raise SystemExit(1)
