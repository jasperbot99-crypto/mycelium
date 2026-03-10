"""Unit tests for predicate resolution."""

from mycelium.domain.predicates import (
    get_canonical_predicates,
    is_canonical,
    resolve_predicate,
)


class TestResolvePredicate:
    def test_exact_canonical(self) -> None:
        assert resolve_predicate("has_status") == "has_status"

    def test_alias(self) -> None:
        assert resolve_predicate("is_down") == "has_status"
        assert resolve_predicate("unavailable") == "has_status"
        assert resolve_predicate("offline") == "has_status"

    def test_case_insensitive(self) -> None:
        assert resolve_predicate("IS_DOWN") == "has_status"
        assert resolve_predicate("Deprecated") == "deprecated"
        assert resolve_predicate("MOVED_TO") == "moved_to"

    def test_whitespace_stripped(self) -> None:
        assert resolve_predicate("  is_down  ") == "has_status"

    def test_unknown_returns_none(self) -> None:
        assert resolve_predicate("some_random_predicate") is None
        assert resolve_predicate("") is None

    def test_all_canonical_predicates_resolve_to_themselves(self) -> None:
        for canonical in get_canonical_predicates():
            assert resolve_predicate(canonical) == canonical

    def test_moved_to_aliases(self) -> None:
        assert resolve_predicate("relocated_to") == "moved_to"
        assert resolve_predicate("migrated_to") == "moved_to"

    def test_deprecated_aliases(self) -> None:
        assert resolve_predicate("end_of_life") == "deprecated"
        assert resolve_predicate("eol") == "deprecated"

    def test_prefers_aliases(self) -> None:
        assert resolve_predicate("favors") == "prefers"
        assert resolve_predicate("preference_is") == "prefers"

    def test_has_limit_aliases(self) -> None:
        assert resolve_predicate("rate_limit") == "has_limit"
        assert resolve_predicate("quota_is") == "has_limit"

    def test_depends_on_aliases(self) -> None:
        assert resolve_predicate("requires") == "depends_on"
        assert resolve_predicate("needs") == "depends_on"

    def test_version_is_aliases(self) -> None:
        assert resolve_predicate("running_version") == "version_is"

    def test_configured_as_aliases(self) -> None:
        assert resolve_predicate("config_is") == "configured_as"
        assert resolve_predicate("set_to") == "configured_as"

    def test_located_at_aliases(self) -> None:
        assert resolve_predicate("path_is") == "located_at"
        assert resolve_predicate("url_is") == "located_at"
        assert resolve_predicate("endpoint_is") == "located_at"

    def test_owned_by_aliases(self) -> None:
        assert resolve_predicate("maintained_by") == "owned_by"
        assert resolve_predicate("managed_by") == "owned_by"


class TestIsCanonical:
    def test_canonical(self) -> None:
        assert is_canonical("has_status") is True
        assert is_canonical("deprecated") is True

    def test_alias_is_not_canonical(self) -> None:
        assert is_canonical("is_down") is False
        assert is_canonical("eol") is False

    def test_unknown(self) -> None:
        assert is_canonical("foo") is False


class TestGetCanonicalPredicates:
    def test_returns_all_ten(self) -> None:
        predicates = get_canonical_predicates()
        assert len(predicates) == 10
        assert "has_status" in predicates
        assert "owned_by" in predicates
