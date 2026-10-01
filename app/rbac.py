"""Clearance logic. Pure functions, no database, easy to test.

Rule (superset): a user may read an item whose level is the same or LOWER
than their own. Levels come from config.clearance.levels, lowest first.

Fail-safe behaviour: anything unknown is denied.
  - unknown user level   -> no access at all
  - unknown item level   -> never matches, so never returned
"""
from __future__ import annotations


def allowed_levels(user_level: str, levels: list[str]) -> list[str]:
    """Levels this user may read. Empty list if user_level is not recognised."""
    if user_level not in levels:
        return []
    return levels[: levels.index(user_level) + 1]


def can_read(user_level: str, item_level: str, levels: list[str]) -> bool:
    return item_level in allowed_levels(user_level, levels)


def initial_level(default: str, levels: list[str]) -> str:
    """Level a NEW upload starts at. Uploaders never choose it.

    Anyone may add knowledge, but not everyone may read it, so new items start
    at config.clearance.default_upload_level ("highest" = the top level, which
    fails safe) until a reviewer relabels them.
    """
    level = levels[-1] if default == "highest" else default
    if level not in levels:
        raise ValueError(f"Unknown clearance level: {level}")
    return level


def can_relabel(user_level: str, levels: list[str]) -> bool:
    """Only the top level may change an item's level (and only to levels they can read)."""
    return bool(levels) and user_level == levels[-1]
