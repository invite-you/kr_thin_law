"""Offline test helpers extracted from the original batch collector."""
from __future__ import annotations

import hashlib
import json
from law_mcp.capture_ledger import CaptureLedger


def request_key(url: str, params: dict) -> str:
    stable = json.dumps({"url": url, "params": sorted(params.items())}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def batch_collect(jobs: list[tuple[str, str, dict]], fetch, ledger: CaptureLedger):
    """Collect (label, url, params) jobs with one fetch per unique request.

    Returns (rows_by_label, duplicate_count).
    """
    rows_by_label: dict[str, dict] = {}
    unique_rows: dict[str, dict] = {}
    duplicates = 0
    for label, url, params in jobs:
        key = request_key(url, params)
        if key in unique_rows:
            rows_by_label[label] = unique_rows[key]
            duplicates += 1
            continue
        raw = fetch(url, params)
        row = ledger.record(url=url, request=params, raw=raw)
        unique_rows[key] = row
        rows_by_label[label] = row
    return rows_by_label, duplicates
