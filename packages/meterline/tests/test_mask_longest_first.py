"""B1-c: ``mask`` replaces the longest secret first, so a secret that contains another is hidden whole whatever the order
the secrets are listed in. Before, ``mask('abc-longsecret-xyz abc', ['abc', 'abc-longsecret'])`` left the tail of the
longer secret in the text: ``'***-longsecret-xyz ***'``."""

import pytest

from meterline.http import mask

TEXT = "abc-longsecret-xyz abc"
WANT = "***-xyz ***"


@pytest.mark.parametrize("secrets", [["abc", "abc-longsecret"], ["abc-longsecret", "abc"]])
def test_mask_longest_first(secrets):
    assert mask(TEXT, secrets) == WANT


@pytest.mark.parametrize("secrets", [["abc", "abc-longsecret"], ["abc-longsecret", "abc"]])
def test_mask_longest_first_in_keys_and_nested_values(secrets):
    value = {TEXT: [TEXT, {"k": TEXT}], "n": 3}
    assert mask(value, secrets) == {WANT: [WANT, {"k": WANT}], "n": 3}


def test_mask_ignores_empty_and_repeated_secrets():
    assert mask(TEXT, ["", None, "abc", "abc"]) == "***-longsecret-xyz ***"
