"""Structural fingerprints for comparing historical alpha expressions."""

from __future__ import annotations

import hashlib
import re
from collections import Counter

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[-+*/]|\d+(?:\.\d+)?")
OPERATOR_HINTS = {
    "rank", "winsorize", "signed_power", "vector_neut", "trade_when",
    "ts_mean", "ts_decay_linear", "ts_rank", "ts_backfill", "group_backfill",
    "group_neutralize", "group_rank", "group_zscore", "normalize", "quantile",
    "zscore", "hump", "ts_corr", "ts_regression", "ts_std_dev", "ts_delta",
    "ts_delay", "ts_sum", "ts_product", "add", "sub", "mul", "div", "max", "min",
}


def fingerprint(expression: str) -> dict:
    """Return deterministic structural metadata without evaluating the expression."""
    text = " ".join((expression or "").split())
    tokens = TOKEN_RE.findall(text)
    operators = [token for token in tokens if token in OPERATOR_HINTS]
    numbers = [token for token in tokens if token.replace(".", "", 1).isdigit()]
    fields = [
        token for token in tokens
        if re.match(r"^[A-Za-z_]", token)
        and token not in OPERATOR_HINTS
        and token not in {"true", "false"}
    ]
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "#", text.lower())
    normalized = re.sub(r"\s+", "", normalized)
    return {
        "hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "normalized": normalized,
        "operators": sorted(Counter(operators).keys()),
        "operator_count": len(operators),
        "fields": sorted(set(fields)),
        "field_count": len(set(fields)),
        "numeric_literals": numbers,
    }
