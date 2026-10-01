"""Who may use which page. Pure functions, easy to test.

SECURITY: these decide what the SERVER allows. Hiding something in the page is cosmetic.

A user's role is their clearance level (e.g. "super", "employee"). Roles come from signed-in
accounts (app/auth.py). Nobody signed in has no role and therefore no access.

  Ask page / POST /api/ask      sign-in + role check: the role must be listed in ui.ask_roles
  Add page / POST /api/upload   NO sign-in and NO role check, by design: anyone can add knowledge.
                                Safe-by-default behaviour comes from elsewhere: uploads start at the
                                top clearance level (unreadable to lower roles until a reviewer
                                relabels them), size and type limits, and the service switch.
  Costs                         top clearance level only
"""
from __future__ import annotations

from .config import UI


def can_ask(role: str | None, ui: UI) -> bool:
    return role is not None and role in ui.ask_roles


def can_view_costs(role: str | None, levels: list[str]) -> bool:
    return bool(levels) and role == levels[-1]
