"""Compatibility CLI; execution is owned by the unified runtime."""


def main(argv=None):
    from runtime_main import legacy_entry
    return legacy_entry('mineral_approach.py', argv)


if __name__=='__main__':
    raise SystemExit(main())
