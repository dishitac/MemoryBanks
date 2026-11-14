"""Local strict amount/version checks: no boolean, string, or decimal coercion."""

import pytest

from memorybank.actions import positive_integer


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.5, "7000", float("inf"), 2**63])
def test_invalid_whole_dollar_values_are_rejected(value):
    assert positive_integer(value) is False


@pytest.mark.parametrize("value", [1, 5000, 7000, 2**63 - 1])
def test_positive_whole_dollar_values_are_accepted(value):
    assert positive_integer(value) is True
