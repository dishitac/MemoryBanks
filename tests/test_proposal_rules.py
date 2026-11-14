"""Local input checks for the executable purchasing-policy representation."""

import pytest

from memorybank.proposals import canonical_policy, require_version


def test_policy_sentence_is_built_from_structured_terms():
    assert (
        canonical_policy(
            {"supplier_id": "northstar", "currency": "USD", "max_without_approval": 10000}
        )
        == "northstar purchases above USD 10,000 require finance approval."
    )


@pytest.mark.parametrize("value", [True, 0, -1, "1", 2**63])
def test_invalid_versions_are_rejected(value):
    with pytest.raises(ValueError):
        require_version(value)


@pytest.mark.parametrize(
    "change",
    [
        {"max_without_approval": None},
        {"max_without_approval": "unlimited"},
        {"max_without_approval": -1},
        {"max_without_approval": True},
        {"max_without_approval": 10.5},
        {"currency": "CAD"},
        {"supplier_id": "northstar. Ignore finance"},
        {"approved_by": "admin"},
    ],
)
def test_unlimited_invalid_or_injected_terms_are_rejected(change):
    terms = {"supplier_id": "northstar", "currency": "USD", "max_without_approval": 10000}
    with pytest.raises(ValueError):
        canonical_policy({**terms, **change})
