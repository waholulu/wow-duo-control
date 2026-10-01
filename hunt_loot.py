"""Compatibility CLI; execution is owned by the unified runtime."""


def main(argv=None):
    from runtime_main import legacy_entry
    return legacy_entry('hunt_loot.py', argv)


if __name__=='__main__':
    raise SystemExit(main())
