"""Bounded official HTTP transport. Each attempt preserves raw evidence.

There is no response cache and no automatic fallback to offline evidence.
Credentials are added only to the outbound request, never to the capture ledger.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .capture_ledger import CaptureLedger
from .card_source import (
    ProviderResponseError,
    SEARCH_URL,
    SERVICE_URL,
    _provider_declared_failure,
    _require_xml_payload,
)

ALLOWED_URLS = (SERVICE_URL, SEARCH_URL)


class OfficialClient:
    """Read-only official source client with a bounded, observable retry policy."""

    def __init__(
        self,
        oc: str | None = None,
        *,
        capture_dir: str | Path | None = None,
        timeout: float = 15,
        max_attempts: int = 3,
        retry_delay: float = 0.5,
        max_bytes: int = 32 * 1024 * 1024,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("timeout must be finite and within 0..60 seconds")
        if not 1 <= max_attempts <= 5 or not 0 <= retry_delay <= 10:
            raise ValueError("max_attempts must be 1..5 and retry_delay 0..10 seconds")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.oc = oc if oc is not None else os.environ.get("LAW_API_OC", "")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.max_bytes = max_bytes
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.capture_dir = Path(capture_dir).resolve() if capture_dir is not None else None
        self.ledger = CaptureLedger(self.capture_dir / "ledger.jsonl") if self.capture_dir else None
        self.attempts: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def _call(self, url: str, params: dict[str, Any]) -> bytes:
        if url not in ALLOWED_URLS:
            raise ValueError("Only the configured official law service is allowed")
        if not self.oc:
            raise ProviderResponseError(
                "API_CREDENTIAL_MISSING", "Set LAW_API_OC to the issued API value", retryable=False
            )
        if any(key.lower() == "oc" for key in params):
            raise ValueError("Pass OC via LAW_API_OC or OfficialClient, not query arguments")
        clean = {key: str(value) for key, value in params.items()}
        query = urllib.parse.urlencode({**clean, "type": "XML", "OC": self.oc})
        request = urllib.request.Request(
            url + "?" + query,
            headers={"User-Agent": "legal-thin-mcp/4.9.0", "Accept": "application/xml"},
        )
        with self._lock:
            for attempt in range(1, self.max_attempts + 1):
                raw = b""
                error: ProviderResponseError | None = None
                started = time.monotonic()
                status = None
                try:
                    with self.opener(request, timeout=self.timeout) as response:
                        status = getattr(response, "status", 200)
                        raw = response.read(self.max_bytes + 1)
                    # Provider payloads can echo OC inside links. Redact before
                    # any validation error is constructed so passthrough errors
                    # never expose the credential.
                    raw = raw.replace(
                        b"OC=" + self.oc.encode("utf-8"),
                        b"OC=REDACTED",
                    )
                    if len(raw) > self.max_bytes:
                        raise ProviderResponseError(
                            "RESPONSE_TOO_LARGE", "Response exceeds the capture size limit", retryable=False
                        )
                    _require_xml_payload(raw)
                    try:
                        ET.fromstring(raw)
                    except ET.ParseError as exc:
                        raise ProviderResponseError(
                            "PARSE_ERROR",
                            "Provider XML is malformed",
                            retryable=True,
                            provider_response=raw.decode("utf-8", errors="replace"),
                        ) from exc
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    raw = exc.read(self.max_bytes + 1)
                    raw = raw.replace(
                        b"OC=" + self.oc.encode("utf-8"),
                        b"OC=REDACTED",
                    )
                    declared = _provider_declared_failure(raw) or {}
                    error = ProviderResponseError(
                        "HTTP_ERROR",
                        f"Official service returned HTTP {exc.code}",
                        retryable=exc.code in {408, 429, 500, 502, 503, 504},
                        provider_code=str(declared.get("provider_code") or ""),
                        provider_message=str(declared.get("provider_message") or ""),
                        provider_fields=declared.get("provider_fields") or {},
                        http_status=exc.code,
                        provider_response=raw.decode("utf-8", errors="replace"),
                    )
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    # Exception messages can contain the credential-bearing URL.
                    error = ProviderResponseError(
                        "TRANSPORT_ERROR", f"Official service transport failed ({type(exc).__name__})",
                        retryable=True,
                    )
                except ProviderResponseError as exc:
                    if exc.http_status is None:
                        exc.http_status = status
                    error = exc
                # raw has already been credential-redacted immediately after I/O.
                row: dict[str, Any] = {
                    "attempt_id": uuid.uuid4().hex,
                    "url": url,
                    "request": clean,
                    "attempt": attempt,
                    "http_status": status,
                    "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "clock_source": "local",
                    "status": error.error_code if error else "OK",
                    "retryable": error.retryable if error else False,
                }
                if error is not None:
                    row["detail"] = error.detail
                    if error.provider_code:
                        row["provider_code"] = error.provider_code
                    if error.provider_message:
                        row["provider_message"] = error.provider_message
                if self.capture_dir is not None:
                    self.capture_dir.mkdir(parents=True, exist_ok=True)
                    raw_path = self.capture_dir / (row["attempt_id"] + ".xml")
                    raw_path.write_bytes(raw)
                    row["raw_path"] = str(raw_path)
                    with (self.capture_dir / "attempts.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    if self.ledger is not None:
                        self.ledger.record(
                            url=url, request=clean, raw=raw,
                            retrieved_at=row["observed_at"], raw_path=str(raw_path),
                        )
                self.attempts.append(row)
                if error is None:
                    return raw
                if not error.retryable or attempt == self.max_attempts:
                    raise error
                self.sleep(self.retry_delay * (2 ** (attempt - 1)))
        raise RuntimeError("Unreachable retry state")


class FixtureClient:
    """Explicit offline replay. A manifest binds request parameters to raw XML files."""

    def __init__(self, manifest_path: str | Path, *, allowed_root: str | Path | None = None) -> None:
        manifest_path = Path(manifest_path).resolve()
        self.root = manifest_path.parent
        self.allowed_root = Path(allowed_root).resolve() if allowed_root is not None else self.root
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.rows = manifest["requests"]
        self.calls: list[dict[str, Any]] = []

    def _call(self, url: str, params: dict[str, Any]) -> bytes:
        if url not in ALLOWED_URLS:
            raise ValueError("Only the configured official law service is allowed")
        self.calls.append(dict(params))
        clean = {key: str(value) for key, value in params.items() if key != "type"}
        matches = [row for row in self.rows if {
            key: str(value) for key, value in row["params"].items() if key != "type"
        } == clean]
        if len(matches) != 1:
            raise ProviderResponseError(
                "FIXTURE_REQUEST_NOT_FOUND", "Offline request must match exactly one fixture", retryable=False
            )
        path = (self.root / matches[0]["file"]).resolve()
        if not path.is_relative_to(self.allowed_root):
            raise ProviderResponseError(
                "FIXTURE_PATH_OUTSIDE_ROOT", "Offline fixture is outside the explicitly allowed root", retryable=False
            )
        raw = path.read_bytes()
        expected = matches[0].get("sha256")
        if expected and hashlib.sha256(raw).hexdigest() != expected:
            raise ProviderResponseError(
                "FIXTURE_HASH_MISMATCH", "Offline fixture changed since binding", retryable=False
            )
        return raw
