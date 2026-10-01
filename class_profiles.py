"""Class-scoped combat settings; uncalibrated characters cannot send input."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_class(name):
    path = ROOT / 'classes' / name / 'combat.json'
    spec = json.loads(path.read_text())
    if spec.get('class') != name:
        raise ValueError('Class profile identity mismatch')
    return spec


def policy_options(spec):
    if spec.get('bindings_verified') is not True or spec.get('vision_verified') is not True:
        raise ValueError(spec['class'] + ': character bindings and vision calibration required')
    options = spec['policy']
    key, interval = options.get('attack_key'), options.get('attack_interval')
    if type(key) is not int or not 4 <= key <= 231:
        raise ValueError('Invalid class attack key')
    if type(interval) not in (int, float) or not math.isfinite(interval) or not .2 <= interval <= 10:
        raise ValueError('Invalid class attack interval')
    low, high = options.get('mana_rest_at'), options.get('mana_resume_at')
    if (type(low) not in (int, float) or type(high) not in (int, float)
            or not 0 < low < high <= 1):
        raise ValueError('Invalid class mana thresholds')
    once=options.get('attack_once',False)
    buff=options.get('precombat_key')
    wait=options.get('precombat_wait',1.6)
    interact=options.get('interact_key')
    if interact is not None and (not once or type(interact) is not int or not 4 <= interact <= 231):
        raise ValueError('Invalid class interact key')
    if type(once) is not bool or (buff is not None and (type(buff) is not int or not 4 <= buff <= 231)):
        raise ValueError('Invalid class attack mode or precombat key')
    if type(wait) not in (int,float) or not math.isfinite(wait) or not .2 <= wait <= 10:
        raise ValueError('Invalid class precombat wait')
    maintain=options.get('maintain_precombat_buff',False)
    refresh=options.get('buff_refresh_seconds',24)
    if type(maintain) is not bool or (maintain and buff is None):
        raise ValueError('Invalid maintained buff configuration')
    if type(refresh) not in (int,float) or not math.isfinite(refresh) or not 5<=refresh<=120:
        raise ValueError('Invalid buff refresh interval')
    opener=options.get('opener_key');cooldown=options.get('opener_cooldown',10.2)
    if opener is not None and (type(opener) is not int or not 4<=opener<=231 or not once):raise ValueError('Invalid opener key')
    if type(cooldown) not in (int,float) or not math.isfinite(cooldown) or not 1<=cooldown<=120:raise ValueError('Invalid opener cooldown')
    reserve=options.get('opener_min_mana',.35)
    effect_timeout=options.get('opener_effect_timeout',3.0)
    log_names=options.get('opener_log_names',[])
    cast_timeout=options.get('attack_effect_timeout',6.0)
    if type(cast_timeout) not in (int,float) or not math.isfinite(cast_timeout) or not interval<cast_timeout<=15:raise ValueError('Invalid attack effect timeout')
    if type(reserve) not in (int,float) or not math.isfinite(reserve) or not 0<reserve<=1:raise ValueError('Invalid opener reserve')
    if type(effect_timeout) not in (int,float) or not math.isfinite(effect_timeout) or not 1<=effect_timeout<=5:raise ValueError('Invalid effect timeout')
    if not isinstance(log_names,list) or any(not isinstance(n,str) or not n.strip() for n in log_names):raise ValueError('Invalid opener log names')
    return dict(attack_key=key, attack_interval=interval, mana_rest_at=low, mana_resume_at=high,
                attack_once=once, precombat_key=buff, precombat_wait=wait, interact_key=interact,
                maintain_precombat_buff=maintain,buff_refresh_seconds=refresh,
                opener_key=opener,opener_cooldown=cooldown,opener_min_mana=reserve,
                opener_log_names=log_names,opener_effect_timeout=effect_timeout,attack_effect_timeout=cast_timeout)
