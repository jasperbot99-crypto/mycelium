"""Predicate resolution — canonical alias table + lookup.

ARCHITECTURE.md Section 4, Spec section 6.1.1a.

Canonical predicates are a fixed enum for system operations. Free-text predicates
are matched via embedding similarity (handled elsewhere). This module handles the
deterministic alias lookup only.
"""

from __future__ import annotations

# Canonical predicates and their aliases — spec section 6.1.1a.
# Each canonical predicate maps to a set of known aliases.
# Alias matching is case-insensitive.
CANONICAL_PREDICATES: dict[str, frozenset[str]] = {
    "has_status": frozenset({
        "has_status",
        "is_down",
        "is_up",
        "is_healthy",
        "is_degraded",
        "is_unavailable",
        "unavailable",
        "offline",
        "online",
        "status_is",
        "status",
    }),
    "moved_to": frozenset({
        "moved_to",
        "relocated_to",
        "migrated_to",
        "endpoint_changed_to",
        "redirects_to",
    }),
    "deprecated": frozenset({
        "deprecated",
        "is_deprecated",
        "no_longer_supported",
        "end_of_life",
        "eol",
        "sunset",
    }),
    "prefers": frozenset({
        "prefers",
        "preference_is",
        "favors",
        "default_choice_is",
        "likes",
    }),
    "has_limit": frozenset({
        "has_limit",
        "rate_limit",
        "rate_limit_is",
        "quota_is",
        "max_is",
        "limit_is",
        "threshold_is",
    }),
    "depends_on": frozenset({
        "depends_on",
        "requires",
        "needs",
        "runtime_dependency",
        "dependency_is",
    }),
    "version_is": frozenset({
        "version_is",
        "version",
        "running_version",
        "current_version",
        "at_version",
    }),
    "configured_as": frozenset({
        "configured_as",
        "config_is",
        "setting_is",
        "set_to",
        "configuration_value",
    }),
    "located_at": frozenset({
        "located_at",
        "path_is",
        "url_is",
        "endpoint_is",
        "file_path",
        "hosted_at",
    }),
    "owned_by": frozenset({
        "owned_by",
        "maintained_by",
        "responsible_is",
        "owner_is",
        "managed_by",
    }),
}

# Reverse lookup: alias → canonical predicate (built once at import time)
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical, aliases in CANONICAL_PREDICATES.items():
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


def resolve_predicate(predicate: str) -> str | None:
    """Resolve a predicate string to its canonical form via alias lookup.

    Returns the canonical predicate name if found, None otherwise.
    Matching is case-insensitive and uses exact string matching against the alias table.

    Free-text predicates that don't match any alias return None — these are
    matched via embedding similarity at query/contradiction time, not here.
    """
    return _ALIAS_TO_CANONICAL.get(predicate.lower().strip())


def is_canonical(predicate: str) -> bool:
    """Check if a predicate is already a canonical predicate name."""
    return predicate.lower().strip() in CANONICAL_PREDICATES


def get_canonical_predicates() -> list[str]:
    """Return all canonical predicate names."""
    return list(CANONICAL_PREDICATES.keys())
