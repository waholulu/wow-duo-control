"""Chinese one-node gathering menu; delegates all checks to the unified runner."""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def choose(label, options):
    print(label)
    for number,name in options.items():
        print(f'  {number}. {name}')
    answer=input('选择：').strip()
    if answer not in options:
        raise ValueError('选项无效，未启动')
    return answer


def main():
    print('原地采矿／采药第一版：一次只处理一个资源，人物须由人放在已验证的安全站位。')
    character={'1':'paladin','2':'warlock'}[choose('角色',{'1':'圣骑士','2':'术士'})]
    resource={'1':'mining','2':'herbalism'}[choose('资源',{'1':'采矿','2':'采药'})]
    action=choose('操作',{'1':'只读预检','2':'现场单次试验'})
    duration_text=input('本次时限秒数（60–90，回车默认60）：').strip()
    duration=int(duration_text) if duration_text else 60
    if not 60<=duration<=90:
        raise ValueError('时限需在60–90秒，未启动')
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output=ROOT/'runs'/f'gather-station-{stamp}'
    common=['--class',character,'--resource',resource,'--trial','--max-seconds',str(duration),'--output',str(output)]
    if action=='1':
        args=['--task','preflight','--check-task','gather-station',*common]
    else:
        if input('人物已在实测安全站位，点击不会使其自动走动？输入“确认”继续：').strip()!='确认':
            print('站位未确认，未启动。')
            return 1
        args=['--task','gather-station','--execute','--station-confirmed',*common]
    code=subprocess.call([sys.executable,str(ROOT/'wow_control.py'),*args],cwd=ROOT)
    report=output/('preflight.json' if action=='1' else 'result.json')
    if report.exists():
        data=json.loads(report.read_text())
        print(json.dumps(data if action=='1' else {'status':data.get('status'),'reason':data.get('reason')},
                         ensure_ascii=False,indent=2))
    print('运行记录：',output)
    return code


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,KeyboardInterrupt) as exc:
        print('\n未启动原地采集：',exc)
        raise SystemExit(1)
