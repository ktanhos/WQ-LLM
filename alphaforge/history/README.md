# Historical Alpha Intelligence

This package imports submitted alpha history from WorldQuant BRAIN into the research database.

## Source

The adapter uses the current user's BRAIN endpoint:

`/users/self/alphas`

Records are requested newest first with `status!=UNSUBMITTED` and `hidden=false`.

## Default window

When no dates are supplied, the importer scans the most recent 30 calendar days.

## Usage

```python
from alphaforge.history.submitted import get_submitted_alphas

rows = get_submitted_alphas(
    session,
    api_base_url="https://api.worldquantbrain.com",
    start_date="2026-08-01",
    end_date="2026-08-22",
)
```

The importer preserves unsuccessful submission states as well as successful ones. This is intentional: research history must include failures to reduce repeated experimentation and survivorship bias.

The next integration step is to connect these records to the Research Layer, expression fingerprints, alpha lineage, and the web dashboard.
