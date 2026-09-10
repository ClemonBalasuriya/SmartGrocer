"""
Sri Lankan festival calendar used as the SARIMAX exogenous regressor
(proposal section 5: "SARIMAX utilising the Sri Lankan festival calendar as
an exogenous variable") and, for consistency, by the synthetic data
generator's seasonal demand boosts.

Dates for movable festivals (Vesak) are fixed approximations for this
implementation - replace FESTIVALS with the exact poya calendar for the
years your real data covers before using this for a production forecast.
"""

from __future__ import annotations

from datetime import date

FESTIVALS = [  # (month, day, half_width_days, peak_multiplier)
    (4, 14, 6, 1.8),   # Sinhala & Tamil New Year
    (5, 22, 4, 1.4),   # Vesak (approximate fixed date)
    (12, 25, 5, 1.6),  # Christmas
]


def festival_multiplier(d: date) -> float:
    """Smooth 1.0..peak multiplier, higher the closer d is to a festival date."""
    mult = 1.0
    for month, day, half_width, peak in FESTIVALS:
        for year in (d.year - 1, d.year, d.year + 1):
            try:
                anchor = date(year, month, day)
            except ValueError:
                continue
            diff = abs((d - anchor).days)
            if diff <= half_width:
                closeness = 1 - diff / (half_width + 1)
                mult = max(mult, 1 + (peak - 1) * closeness)
    return mult


def is_festival_window(d: date, threshold: float = 1.05) -> int:
    """Binary flag used directly as the SARIMAX exogenous variable."""
    return 1 if festival_multiplier(d) > threshold else 0
