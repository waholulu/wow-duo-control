"""Compatibility exports and CLI for the unified loot skill."""
from pathlib import Path

from interaction_vision import LootVision

ROOT=Path(__file__).resolve().parent


def main(argv=None):
    from runtime_main import legacy_entry
    return legacy_entry('loot_probe.py', argv)


if __name__=='__main__':
    raise SystemExit(main())
