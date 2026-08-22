"""Submitted alpha history adapter for WorldQuant BRAIN."""

from datetime import date, timedelta
from typing import Any


DEFAULT_LIMIT = 100


def _iso(value: Any) -> str:
    if value is None:
        return ""
    return str(value)[:10]


def get_submitted_alphas(
    session,
    api_base_url: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Download submitted/non-unsubmitted alphas for the current user.

    Results are ordered newest first.  When a start date is supplied the
    pagination stops as soon as an older record is reached.
    """
    if start_date is None:
        start_date = (date.today() - timedelta(days=30)).isoformat()
    if end_date is None:
        end_date = date.today().isoformat()

    rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        url = (
            f"{api_base_url.rstrip('/')}/users/self/alphas"
            f"?limit={limit}"
            f"&offset={offset}"
            f"&status!=UNSUBMITTED"
            f"&order=-dateSubmitted"
            f"&hidden=false"
        )
        response = session.get(url)
        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code}\n{response.text}"
            )

        payload = response.json()
        results = payload.get("results", [])
        if not results:
            break

        stop = False
        for alpha in results:
            submitted = _iso(alpha.get("dateSubmitted"))
            if start_date and submitted and submitted < start_date:
                stop = True
                break
            if not submitted:
                continue
            if end_date and submitted > end_date:
                continue

            settings = alpha.get("settings") or {}
            regular = alpha.get("regular") or {}
            is_result = alpha.get("is") or {}

            rows.append(
                {
                    "alpha_id": alpha.get("id"),
                    "submitted": submitted,
                    "status": alpha.get("status"),
                    "region": settings.get("region"),
                    "universe": settings.get("universe"),
                    "delay": settings.get("delay"),
                    "expression": regular.get("code"),
                    "sharpe": is_result.get("sharpe"),
                    "fitness": is_result.get("fitness"),
                    "returns": is_result.get("returns"),
                    "turnover": is_result.get("turnover"),
                    "margin": is_result.get("margin"),
                    "source": "brain_submitted",
                }
            )

        if stop or payload.get("next") is None:
            break
        offset += limit

    return rows
