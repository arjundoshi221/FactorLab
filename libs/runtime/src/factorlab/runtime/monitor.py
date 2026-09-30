"""Best-effort UDP monitoring protocol, shared by senders and receivers."""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections.abc import Callable
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic_settings import SettingsConfigDict

from factorlab.core.logging import redact
from factorlab.core.settings import FactorLabSettings

MAX_PACKET = 1200
State = Literal["starting", "running", "idle", "stopping"]
_INSTANCE_ID = str(uuid4())
_STARTED = time.monotonic()
_PACKET_LOCK = threading.Lock()
_SEQUENCES: dict[str, int] = {}


class MonitorSettings(FactorLabSettings):
    model_config = SettingsConfigDict(env_prefix="FACTORLAB_")
    monitor_addr: str = ""
    service: str = ""


def decode_packet(data: bytes) -> dict[str, Any]:
    """Validate v1 without rejecting future optional fields."""
    if len(data) > MAX_PACKET:
        raise ValueError("oversized packet")
    raw: object = json.loads(data)
    if not isinstance(raw, dict):
        raise ValueError("packet must be an object")
    p = cast(dict[str, Any], raw)
    if type(p.get("v")) is not int or p["v"] != 1:
        raise ValueError("unsupported version")
    if p.get("type") not in ("hb", "warn", "err"):
        raise ValueError("unsupported type")
    for key in ("id", "inst", "host"):
        if not isinstance(p.get(key), str) or not p[key] or len(p[key]) > 128:
            raise ValueError(f"invalid {key}")
    if UUID(p["inst"]).version != 4:
        raise ValueError("invalid instance UUID")
    for key in ("seq", "ts"):
        if type(p.get(key)) is not int or not 0 <= p[key] < 2**63:
            raise ValueError(f"invalid {key}")
    if p["type"] == "hb":
        interval: Any = p.get("interval_s")
        if (
            type(interval) not in (int, float)
            or not math.isfinite(interval)
            or not 0 < interval <= 86400
            or p.get("state") not in ("starting", "running", "idle", "stopping")
        ):
            raise ValueError("invalid heartbeat")
        uptime: Any = p.get("uptime_s", 0)
        if type(uptime) not in (int, float) or not math.isfinite(uptime) or not 0 <= uptime <= 1e9:
            raise ValueError("invalid uptime")
    else:
        code = p.get("error_type")
        if (
            not isinstance(code, str)
            or not code
            or len(code) > 64
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in code)
        ):
            raise ValueError("invalid error_type")
        if not isinstance(p.get("message"), str) or len(p["message"]) > 512:
            raise ValueError("invalid message")
        if p["type"] == "err" and p.get("severity") not in ("error", "fatal"):
            raise ValueError("invalid severity")
        context: Any = p.get("context", {})
        if not isinstance(context, dict):
            raise ValueError("context must be flat")
        if any(
            not isinstance(k, str)
            or type(v) not in (str, int, float, bool, type(None))
            or (isinstance(v, float) and not math.isfinite(v))
            for k, v in cast(dict[Any, Any], context).items()
        ):
            raise ValueError("context must be flat")
        if "fingerprint" in p and not isinstance(p["fingerprint"], str):
            raise ValueError("invalid fingerprint")
    return p


class MonitorClient:
    """Never waits for DNS or delivery in the caller's loop; never raises."""

    def __init__(self, id: str | None = None, *, interval_s: float = 30) -> None:
        self.id = id or ""
        self.interval_s = interval_s
        self.inst = _INSTANCE_ID
        self.started = _STARTED
        self._socket: socket.socket | None = None
        self._target: tuple[str, int] | None = None
        self._lock = _PACKET_LOCK
        self._host = socket.gethostname()
        try:
            settings = MonitorSettings()
            self.id = self.id or settings.service
            if settings.monitor_addr:
                threading.Thread(
                    target=self._resolve, args=(settings.monitor_addr,), daemon=True
                ).start()
        except Exception:  # noqa: BLE001 - monitoring must never affect the caller
            pass

    @property
    def seq(self) -> int:
        return _SEQUENCES.get(self.id, 0)

    @seq.setter
    def seq(self, value: int) -> None:
        _SEQUENCES[self.id] = value

    def _resolve(self, address: str) -> None:
        while True:
            try:
                host, port = address.rsplit(":", 1)
                target = socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_DGRAM)[0][
                    4
                ]
                if self._socket is None:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.setblocking(False)
                    self._socket = sock
                self._target = (str(target[0]), int(target[1]))
            except Exception:  # noqa: BLE001 - retry DNS off the caller thread
                pass
            time.sleep(30)

    def _send(self, kind: str, **fields: Any) -> None:
        try:
            if self._socket is None or self._target is None or not self.id:
                return
            if not self._lock.acquire(blocking=False):
                return
            try:
                self.seq += 1
                packet: dict[str, Any] = dict(
                    v=1,
                    type=kind,
                    id=self.id,
                    inst=self.inst,
                    seq=self.seq,
                    ts=int(time.time() * 1000),
                    host=self._host,
                    **fields,
                )
                if "message" in packet:
                    packet["message"] = redact(str(packet["message"]))[:512]

                def encode() -> bytes:
                    return json.dumps(
                        packet, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                    ).encode()

                data = encode()
                while len(data) > MAX_PACKET and packet.get("message"):
                    packet["message"] = packet["message"][:-1]
                    data = encode()
                if len(data) > MAX_PACKET:
                    packet.pop("context", None)
                    data = encode()
                if len(data) <= MAX_PACKET:
                    self._socket.sendto(data, self._target)
            finally:
                self._lock.release()
        except Exception:  # noqa: BLE001 - advisory delivery never raises
            pass

    def heartbeat(self, state: State = "running") -> None:
        self._send(
            "hb",
            state=state,
            interval_s=self.interval_s,
            uptime_s=int(time.monotonic() - self.started),
        )

    def warn(self, error_type: str, message: str, *, fingerprint: str = "", **ctx: Any) -> None:
        self._send(
            "warn", error_type=error_type, message=message, fingerprint=fingerprint, context=ctx
        )

    def error(
        self,
        error_type: str,
        message: str,
        severity: str = "error",
        *,
        fingerprint: str = "",
        **ctx: Any,
    ) -> None:
        self._send(
            "err",
            error_type=error_type,
            message=message,
            severity=severity,
            fingerprint=fingerprint,
            context=ctx,
        )


_clients: dict[str, MonitorClient] = {}
_clients_lock = threading.Lock()


def monitor_client(service: str = "") -> MonitorClient:
    """One instance and sequence per process/service, including legacy heartbeat aliases."""
    try:
        service = MonitorSettings().service or service
    except Exception:  # noqa: BLE001 - configuration cannot break a sender
        pass
    with _clients_lock:
        if service not in _clients:
            _clients[service] = MonitorClient(service)
        return _clients[service]


class MonitoredEvent(threading.Event):
    """A loop's stop event: report progress and heartbeat during intentional waits."""

    def __init__(self) -> None:
        super().__init__()
        self._monitor: MonitorClient | None = None
        self._next_heartbeat = 0.0

    def _tick(self, state: State) -> None:
        if self._monitor is None:
            self._monitor = monitor_client()
        if time.monotonic() >= self._next_heartbeat:
            self._monitor.heartbeat(state)
            self._next_heartbeat = time.monotonic() + self._monitor.interval_s

    def is_set(self) -> bool:
        result = super().is_set()
        if not result:
            self._tick("running")
        return result

    def set(self) -> None:
        super().set()
        if self._monitor is None:
            self._monitor = monitor_client()
        self._monitor.heartbeat("stopping")

    def wait(self, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + timeout if timeout is not None else float("inf")
        while not super().is_set():
            self._tick("idle")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if super().wait(min(remaining, 1)):
                return True
        return True


def monitoring_sleep(seconds: float, *, should_stop: Callable[[], bool]) -> None:
    """Replace long daemon sleeps with interruptible, monitored intentional waits."""
    client = monitor_client()
    deadline = time.monotonic() + seconds
    next_send = 0.0
    while not should_stop():
        now = time.monotonic()
        if now >= next_send:
            client.heartbeat("idle")
            next_send = now + client.interval_s
        if now >= deadline:
            return
        time.sleep(min(1, deadline - now))
    client.heartbeat("stopping")
