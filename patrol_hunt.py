"""Compatibility CLI; execution is owned by the unified runtime."""

def main(argv=None):
    """Compatibility entry: all live execution goes through the unified runtime."""
    from runtime_main import legacy_entry
    return legacy_entry('patrol_hunt.py', argv)


if __name__ == '__main__':
    raise SystemExit(main())
