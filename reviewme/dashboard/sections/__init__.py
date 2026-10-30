"""One module per dashboard section: `ID`, `TITLE`, `compute(ctx)`, `render(result, ctx)`.

`compute` is pure and returns JSON-serialisable data (served as-is by `/api/dashboard`);
`render` turns it into an HTML fragment. Keeping them apart is what makes the numbers
testable without parsing HTML.
"""
from __future__ import annotations

from . import cost, hall_of_fame, history, quality, reliability, roi, summary, tuning, usage

MODULES = {m.ID: m for m in (summary, quality, usage, cost, roi, reliability, tuning,
                             history, hall_of_fame)}
