"""Provider model lists, newest first.

A provider's catalog runs to a hundred-plus ids, and the useful ones are the
recent ones. Ordering is by the release date the provider itself reported during
Discover; where it reported none, the version and date read out of the id stand
in for it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from routes.models import _id_order_key, _sort_models  # noqa: E402


def _order(ids, dates=None):
    dates = dates or {}
    models = [{"id": i, "released_at": dates.get(i, 0)} for i in ids]
    return [m["id"] for m in _sort_models(models)]


def test_a_reported_release_date_wins_over_everything_in_the_id():
    """Once Discover has asked the provider, the id stops being a guess."""
    assert _order(
        ["gpt-9.9-imaginary", "humble-name"],
        {"humble-name": 1_800_000_000},
    ) == ["humble-name", "gpt-9.9-imaginary"]


def test_undated_models_fall_back_to_the_version_in_the_id():
    assert _order(["gpt-4o", "gpt-5.6-sol", "gpt-5.4-mini", "gpt-3.5-turbo"]) == [
        "gpt-5.6-sol", "gpt-5.4-mini", "gpt-4o", "gpt-3.5-turbo",
    ]


def test_a_bare_family_id_sorts_below_its_own_point_releases():
    """The regression that made fixed-width version tuples necessary: a short
    tuple is a prefix of a long one, and a prefix sorts first."""
    assert _order(["gpt-5", "gpt-5.6-sol", "gpt-5.2"])[0] == "gpt-5.6-sol"
    assert _order(["gpt-5", "gpt-5.6-sol", "gpt-5.2"])[-1] == "gpt-5"


def test_a_date_in_an_id_is_not_read_as_a_version():
    """"gpt-5-2025-08-07" is version 5 dated 2025-08-07, not version 5.8.7."""
    version, date = _id_order_key("gpt-5-2025-08-07")
    assert version == (5, 0, 0, 0)
    assert date == (2025, 8, 7)
    # ...so it stays below 5.6 instead of jumping the whole family.
    assert _order(["gpt-5-2025-08-07", "gpt-5.6-sol"]) == ["gpt-5.6-sol", "gpt-5-2025-08-07"]


def test_snapshots_of_one_family_order_by_their_date():
    assert _order(["gpt-5.4", "gpt-5.4-2026-03-05"]) == ["gpt-5.4-2026-03-05", "gpt-5.4"]
    assert _order([
        "claude-opus-4-1-20250805", "claude-sonnet-4-5-20250929",
    ]) == ["claude-sonnet-4-5-20250929", "claude-opus-4-1-20250805"]


def test_a_versionless_id_sorts_to_the_bottom_rather_than_the_top():
    """A dated-but-versionless id ("gpt-audio-mini-2025-12-15") reads as version
    0. Without the year split its 2025 would have made it the newest thing in
    the catalog."""
    assert _order(["gpt-audio-mini-2025-12-15", "gpt-5.6-sol"])[0] == "gpt-5.6-sol"


def test_the_order_is_stable_for_ties():
    ids = ["b-model", "a-model", "c-model"]
    assert _order(ids) == ["a-model", "b-model", "c-model"]


@pytest.mark.parametrize("model_id", ["", "no-numbers-at-all", "gpt-" * 40])
def test_the_key_never_raises_on_an_odd_id(model_id):
    version, date = _id_order_key(model_id)
    assert len(version) == 4 and len(date) == 3
