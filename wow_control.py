"""Unified launch entry; fish-trial keeps its scene-specific validation explicit."""
import sys


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    task=None
    for i,value in enumerate(argv):
        if value=='--task' and i+1<len(argv):
            task=argv[i+1]
            if task=='fish-trial':del argv[i:i+2]
            break
        if value.startswith('--task='):
            task=value.split('=',1)[1]
            if task=='fish-trial':del argv[i]
            break
    if task=='fish-trial':
        from fishing_trial import main as run
        return run(argv)
    from runtime_main import main as run
    return run(argv)


if __name__=='__main__':raise SystemExit(main())
