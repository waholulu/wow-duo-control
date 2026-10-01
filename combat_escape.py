"""Legacy escape helper plus a compatibility CLI for the unified runtime."""
import time


def escape(observe,box,record,sleep=time.sleep):
    """Bounded historical helper retained for offline regression callers."""
    clear=0
    for key,ms in [(79,500)]*2+[(26,500)]*24:
        obs,age=observe()
        if age>.5 or age<0 or not obs.valid:
            return {'state':'ESCAPE_ABORTED','reason':'unusable_frame'}
        if not obs.in_combat:
            clear+=1
            if clear>=3:return {'state':'ESCAPED','hp':obs.player_hp}
            sleep(.2)
            continue
        clear=0
        record(key=key,ms=ms,hp=obs.player_hp)
        box.tap(key,ms);sleep(.12)
    obs,age=observe()
    return {'state':'ESCAPE_LIMIT','hp':obs.player_hp,'in_combat':obs.in_combat}


def main(argv=None):
    from runtime_main import legacy_entry
    return legacy_entry('combat_escape.py', argv)


if __name__=='__main__':
    raise SystemExit(main())
