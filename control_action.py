from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    key: int
    milliseconds: int
    reason: str
