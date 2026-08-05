"""Filter Ops ProposedFix actions to an allowlist."""

from __future__ import annotations

from swarm.schemas import ProposedFix


def apply_fix_allowlist(fix: ProposedFix, allowlist: list[str] | None) -> ProposedFix:
    """Drop actions whose kind is not in ``allowlist``.

    When ``allowlist`` is None (demo / fixture path), the fix is returned unchanged.
    """

    if allowlist is None:
        return fix
    allowed = {item.lower() for item in allowlist}
    kept = [action for action in fix.actions if action.kind.value.lower() in allowed]
    dropped = [action.kind.value for action in fix.actions if action.kind.value.lower() not in allowed]
    rationale = list(fix.rationale)
    if dropped:
        rationale.append(
            "Filtered disallowed actions for this service: " + ", ".join(sorted(set(dropped)))
        )
    return fix.model_copy(update={"actions": kept, "rationale": rationale})
