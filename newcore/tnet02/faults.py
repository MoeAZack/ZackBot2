"""TNET-02 case-scoped deterministic transport fault injection.

``FaultingSender`` wraps the #291 HTTP sender type.  It is inert unless the adapter arms exactly one fault for the
current case, and it disarms itself after that fault fires once.  It never fabricates a successful answer, never
alters a request, and never touches a request the arming predicate does not select.

* ``DROP_AFTER_ACCEPT``: the real request is forwarded; when the real answer is a 2xx it is discarded and the
  caller sees ``TimeoutError`` (the venue maps that to UNKNOWN).  The exchange really accepted the order.
* ``HTTP_429`` / ``HTTP_418``: the selected request is *not* forwarded; a synthetic rate-limit answer is returned
  (Binance error code -1003 body), so nothing executes on the exchange.
"""
from __future__ import annotations

import enum
import threading
from dataclasses import dataclass
from typing import Callable

from newcore.venue.binance_usdm_testnet import HttpRequest, HttpResponse

from .ports import Clock


class FaultKind(enum.StrEnum):
    DROP_AFTER_ACCEPT = "drop_after_accept"
    HTTP_429 = "http_429"
    HTTP_418 = "http_418"


class FaultError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class FaultRecord:
    kind: FaultKind
    fired_at_ms: int
    forwarded: bool
    upstream_status: int | None


_RATE_BODY = b'{"code":-1003,"msg":"synthetic tnet02 rate limit"}'


class FaultingSender:
    """Callable[[HttpRequest], HttpResponse] with at most one armed, one-shot fault."""

    __test__ = False

    def __init__(self, sender: Callable[[HttpRequest], HttpResponse], clock: Clock):
        if not callable(sender) or not callable(clock):
            raise FaultError("faults.callables")
        self._sender, self._clock = sender, clock
        self._lock = threading.Lock()
        self._armed: tuple[FaultKind, Callable[[HttpRequest], bool], int | None] | None = None
        self._fired: list[FaultRecord] = []

    @property
    def armed(self) -> bool:
        return self._armed is not None

    @property
    def fired(self) -> tuple[FaultRecord, ...]:
        return tuple(self._fired)

    def arm(self, kind: FaultKind, match: Callable[[HttpRequest], bool], *, retry_after_ms: int | None = None) -> None:
        if type(kind) is not FaultKind or not callable(match):
            raise FaultError("faults.arm")
        if kind is FaultKind.HTTP_429:
            if type(retry_after_ms) is not int or not 1 <= retry_after_ms <= 120_000:
                raise FaultError("faults.retry_after")
        elif retry_after_ms is not None:
            raise FaultError("faults.retry_after")
        with self._lock:
            if self._armed is not None:
                raise FaultError("faults.already_armed")
            self._armed = (kind, match, retry_after_ms)

    def disarm(self) -> bool:
        """Disarm; returns True when an armed fault never fired (the caller must treat that as a failed case)."""
        with self._lock:
            unfired = self._armed is not None
            self._armed = None
            return unfired

    def __call__(self, request: HttpRequest) -> HttpResponse:
        with self._lock:
            armed = self._armed
            selected = False
            if armed is not None:
                try:
                    selected = armed[1](request) is True
                except Exception:
                    selected = False
            if selected:
                self._armed = None
        if not selected:
            return self._sender(request)
        kind, _match, retry_after_ms = armed
        if kind is FaultKind.DROP_AFTER_ACCEPT:
            response = self._sender(request)
            status = response.status if isinstance(response, HttpResponse) else None
            accepted = type(status) is int and 200 <= status < 300
            self._fired.append(FaultRecord(kind, self._clock(), True, status))
            if accepted:
                raise TimeoutError("answer dropped by tnet02 fault injection")
            return response
        status = 429 if kind is FaultKind.HTTP_429 else 418
        self._fired.append(FaultRecord(kind, self._clock(), False, None))
        return HttpResponse(status=status, body=_RATE_BODY, retry_after_ms=retry_after_ms, used_weight_1m=None)


def client_id_of(request: HttpRequest) -> str | None:
    """The newClientOrderId / clientAlgoId carried by a request query, if any."""
    for field in request.query.split("&"):
        name, _, value = field.partition("=")
        if name in ("newClientOrderId", "clientAlgoId"):
            return value
    return None


__all__ = ["FaultError", "FaultKind", "FaultRecord", "FaultingSender", "client_id_of"]
