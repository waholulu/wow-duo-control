"""Navigation math plus a compatibility CLI for the unified runtime."""
import math


def steering(position,heading,target):
    desired=math.atan2(target[1]-position[1],target[0]-position[0])
    return (desired-heading+math.pi)%(2*math.pi)-math.pi


def oscillating(positions):
    return (len(positions)>=4 and positions[-1]==positions[-3]
            and positions[-2]==positions[-4] and positions[-1]!=positions[-2])


def main(argv=None):
    from runtime_main import legacy_entry
    return legacy_entry('navigate_local.py', argv)


if __name__=='__main__':
    raise SystemExit(main())
