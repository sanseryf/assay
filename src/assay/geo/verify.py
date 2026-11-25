"""USPS Addresses API v3: opt-in, cached, throttled, and never required.

The offline layer answers "do these fields agree with the ZIP table?" This module
answers the next question — "is this a real, deliverable street address?" — which
only USPS can answer. It is opt-in because it sends address fields to a third party.

Design constraints:

* credentials come from the environment (``USPS_CLIENT_ID`` / ``USPS_CLIENT_SECRET``),
  never from the rules file, so they cannot be committed by accident;
* every result, including "no match", is cached by normalized-address hash;
* requests are throttled to the configured rate, and a verification failure is a
  finding, never a crashed run.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib import parse, request
from urllib.error import URLError

TOKEN_URL = "https://apis.usps.com/oauth2/v3/token"
ADDRESS_URL = "https://apis.usps.com/addresses/v3/address"

Transport = Callable[[request.Request, float], Any]


class VerificationError(Exception):
    """Verification is enabled but cannot run: missing credentials or a bad cache."""


@dataclass(frozen=True, slots=True)
class VerifiedAddress:
    line1: str
    line2: str
    city: str
    state: str
    zip: str
    zip4: str = ""
    deliverable: bool = True

    def as_text(self) -> str:
        unit = f" {self.line2}" if self.line2 else ""
        plus4 = f"-{self.zip4}" if self.zip4 else ""
        return f"{self.line1}{unit}, {self.city}, {self.state} {self.zip}{plus4}"


class Verifier(Protocol):
    def verify(self, address: dict[str, str]) -> VerifiedAddress | None: ...


def _default_transport(req: request.Request, timeout: float) -> Any:
    return request.urlopen(req, timeout=timeout)


def cache_key(address: dict[str, str]) -> str:
    raw = "|".join(
        " ".join(str(address.get(key, "")).upper().split())
        for key in ("line1", "line2", "city", "state", "zip")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class UspsVerifier:
    """A thin client with credentials, a disk cache and a request throttle."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        cache_path: Path | None = None,
        ttl_days: int = 30,
        max_per_minute: int = 30,
        transport: Transport | None = None,
        timeout: float = 15.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not client_id or not client_secret:
            raise VerificationError(
                "USPS verification needs USPS_CLIENT_ID and USPS_CLIENT_SECRET in the "
                "environment; register at https://developers.usps.com/"
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._ttl = ttl_days * 86_400
        self._min_interval = 60.0 / max_per_minute if max_per_minute > 0 else 0.0
        self._transport = transport or _default_transport
        self._timeout = timeout
        self._clock = clock
        self._sleep = sleep
        self._token: tuple[str, float] | None = None
        self._last_request = 0.0

        self._cache_path = cache_path or _default_cache_path()
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = sqlite3.connect(self._cache_path)
        self._cache.execute(
            "CREATE TABLE IF NOT EXISTS verification "
            "(cache_key TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT NOT NULL)"
        )
        self._cache.commit()

    @classmethod
    def from_env(cls, **kwargs: Any) -> UspsVerifier:
        return cls(
            os.environ.get("USPS_CLIENT_ID", ""),
            os.environ.get("USPS_CLIENT_SECRET", ""),
            **kwargs,
        )

    # ------------------------------------------------------------------ public

    def verify(self, address: dict[str, str]) -> VerifiedAddress | None:
        key = cache_key(address)
        found, cached = self._cache_get(key)
        if found:
            return cached  # a VerifiedAddress, or None for "USPS found no match"

        result = self._fetch(address)
        self._cache_put(key, result)
        return result

    def close(self) -> None:
        self._cache.close()

    def __enter__(self) -> UspsVerifier:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ HTTP

    def _fetch(self, address: dict[str, str]) -> VerifiedAddress | None:
        self._throttle()
        token = self._access_token()
        params = parse.urlencode(
            {
                "streetAddress": address.get("line1", ""),
                "secondaryAddress": address.get("line2", ""),
                "city": address.get("city", ""),
                "state": address.get("state", ""),
                "ZIPCode": address.get("zip", "")[:5],
            }
        )
        req = request.Request(
            f"{ADDRESS_URL}?{params}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with self._transport(req, self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, TimeoutError, ValueError) as exc:
            raise VerificationError(f"USPS request failed: {exc}") from exc
        return _parse_response(payload)

    def _access_token(self) -> str:
        if self._token is not None and self._clock() - self._token[1] < 3_000:
            return self._token[0]
        body = parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
        ).encode("ascii")
        req = request.Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self._transport(req, self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, TimeoutError, ValueError) as exc:
            raise VerificationError(f"USPS token request failed: {exc}") from exc
        token = payload.get("access_token")
        if not token:
            raise VerificationError("USPS token response did not contain an access token")
        self._token = (str(token), self._clock())
        return self._token[0]

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        wait = self._min_interval - (self._clock() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()

    # ------------------------------------------------------------------ cache

    def _cache_get(self, key: str) -> tuple[bool, VerifiedAddress | None]:
        """``(found, result)``; a cached miss is ``(True, None)``, not an absence."""
        row = self._cache.execute(
            "SELECT fetched, payload FROM verification WHERE cache_key = ?", (key,)
        ).fetchone()
        if row is None:
            return False, None
        fetched, payload = row
        if self._clock() - float(fetched) > self._ttl:
            return False, None
        data = json.loads(payload)
        if data is None:
            return True, None
        return True, VerifiedAddress(**data)

    def _cache_put(self, key: str, result: VerifiedAddress | None) -> None:
        payload = json.dumps(asdict(result) if result is not None else None)
        self._cache.execute(
            "INSERT OR REPLACE INTO verification (cache_key, fetched, payload) VALUES (?, ?, ?)",
            (key, self._clock(), payload),
        )
        self._cache.commit()


def _parse_response(payload: dict[str, Any]) -> VerifiedAddress | None:
    """Map the USPS v3 response to :class:`VerifiedAddress`, ``None`` for no match."""
    address = payload.get("address")
    if not isinstance(address, dict) or not address.get("streetAddress"):
        return None
    info = payload.get("additionalInfo") or {}
    deliverable = True
    if isinstance(info, dict) and "DPVConfirmation" in info:
        deliverable = str(info["DPVConfirmation"]).upper() in {"Y", "S", "D"}
    return VerifiedAddress(
        line1=str(address.get("streetAddress", "")).strip(),
        line2=str(address.get("secondaryAddress", "")).strip(),
        city=str(address.get("city", "")).strip(),
        state=str(address.get("state", "")).strip().upper(),
        zip=str(address.get("ZIPCode", "")).strip(),
        zip4=str(address.get("ZIPPlus4", "")).strip(),
        deliverable=deliverable,
    )


def _default_cache_path() -> Path:
    root = os.environ.get("ASSAY_CACHE_DIR")
    base = Path(root) if root else Path.home() / ".cache" / "assay"
    return base / "usps-verification.sqlite"
