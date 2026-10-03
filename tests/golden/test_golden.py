"""Golden-master parity: current code must reproduce the captured legacy behaviour exactly."""

import json

import pytest

from tests.golden.characterize import FIXTURE_DIR, SUITES, normalize


@pytest.mark.critical
@pytest.mark.parametrize("name", sorted(SUITES))
def test_golden_parity(name):
    expected = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    assert normalize(SUITES[name]()) == expected
