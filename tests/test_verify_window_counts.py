from __future__ import annotations

import pytest

from verify_window_counts import scope_selector


@pytest.mark.parametrize("work_style", ["squash", "squash-merge"])
def test_verifier_counts_squash_and_legacy_squash_merge_the_same_way(
    work_style: str,
) -> None:
    assert scope_selector({"work_style": work_style, "scoped_ref": "main"}) == ["main"]
