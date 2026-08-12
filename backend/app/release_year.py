from __future__ import annotations

from datetime import datetime


# Release year describes the recording/release, not the composition. 1860 is
# the earliest surviving sound recording; one year ahead permits announced
# releases without accepting corrupted values such as 0211 or 1125.
MIN_RELEASE_YEAR = 1860


def is_plausible_release_year(value: int) -> bool:
    return MIN_RELEASE_YEAR <= value <= datetime.now().year + 1
