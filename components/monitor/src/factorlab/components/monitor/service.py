"""Receive-time liveness, notifications, and replayable batched storage."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from factorlab.clickhouse import ClickHouse
from factorlab.runtime.monitor import decode_packet
from factorlab.runtime.notify import notify

log = logging.getLogger(__name__)
EVENT_COLUMNS = [
    "received_at",
    "sent_at",
    "service_id",
    "instance_id",
    "seq",
    "type",
    "origin",
    "state",
    "severity",
    "error_type",
    "message",
    "context_json",
    "host",
    "skew_ms",
]
STATUS_COLUMNS = [
    "service_id",
    "status",
    "last_seen_at",
    "instance_id",
    "instance_started_at",
    "restarts_24h",
    "last_error_at",
    "last_error_type",
    "last_error_message",
    "warn_count_1h",
    "err_count_1h",
    "updated_at",
    "version",
    "packet_loss",
    "registered",
]


def timestamp(ms):
    return datetime.fromtimestamp(ms / 1000, UTC)


class Monitor:
    def __init__(self, registry: dict[str, dict[str, Any]], *, clock=time.time, alert=notify):
        self.registry = registry
        self.clock = clock
        self.alert = alert
        self.started = clock()
        self.services = {}
        self.events = deque(maxlen=10000)
        self.invalid_packets = 0
        self._warned = set()
        self._alerted = {}

    def _new(self, id):
        return {
            "service_id": id,
            "status": "unknown",
            "last_seen_at": None,
            "instance_id": "",
            "instance_started_at": None,
            "last_error_at": None,
            "last_error_type": "",
            "last_error_message": "",
            "packet_loss": 0,
            "registered": id in self.registry,
            "interval_s": self.registry.get(id, {}).get("interval_s", 30),
            "seq": -1,
            "restarts": deque(maxlen=10000),
            "warnings": deque(maxlen=10000),
            "errors": deque(maxlen=10000),
            "retired": deque(maxlen=100),
        }

    def _event(
        self,
        id,
        kind,
        *,
        origin="monitor",
        state="",
        severity="",
        error_type="",
        message="",
        packet=None,
    ):
        p = packet or {}
        now = self.clock()
        self.events.append(
            {
                "received_at": int(now * 1000),
                "sent_at": p.get("ts", int(now * 1000)),
                "service_id": id,
                "instance_id": p.get("inst", self.services.get(id, {}).get("instance_id", "")),
                "seq": p.get("seq", 0),
                "type": kind,
                "origin": origin,
                "state": state,
                "severity": severity,
                "error_type": error_type,
                "message": message,
                "context_json": json.dumps(p.get("context", {})),
                "host": p.get("host", ""),
                "skew_ms": int(now * 1000) - p.get("ts", int(now * 1000)),
            }
        )

    def _alert(self, id, subject, severity, message=""):
        now = self.clock()
        key = (id, subject)
        self._alerted = {k: t for k, t in self._alerted.items() if now - t < 300}
        if key in self._alerted:
            return
        self._alerted[key] = now
        try:
            self.alert(f"{id}: {subject}", message or subject, severity=severity, source=id)
        except Exception:
            log.exception("Notification failed for %s", id)

    def _transition(self, id, state):
        s = self.services[id]
        previous = s["status"]
        if state == previous:
            return
        s["status"] = state
        self._event(id, "state", state=state)
        if state == "down":
            self._alert(id, "down", "fail")
        elif state == "up" and previous in ("late", "down"):
            self._alert(id, "recovered", "info")

    def receive(self, data):
        try:
            p = decode_packet(data)
            timestamp(p["ts"])
        except (ValueError, TypeError, UnicodeError, OverflowError, OSError):
            self.invalid_packets += 1
            if self.invalid_packets == 1 or self.invalid_packets % 100 == 0:
                log.warning(
                    "Invalid UDP packet (total=%s, bytes=%s)", self.invalid_packets, len(data)
                )
            return
        id = p["id"]
        now = self.clock()
        s = self.services.setdefault(id, self._new(id))
        if not s["registered"] and id not in self._warned:
            self._warned.add(id)
            self._event(
                id, "warn", error_type="unregistered", message="Service missing from registry"
            )
            self._alert(id, "unregistered", "warn")
        if p["inst"] in s["retired"]:
            return
        if s["instance_id"] != p["inst"]:
            if s["instance_id"]:
                s["retired"].append(s["instance_id"])
                s["restarts"].append(now)
                self._event(id, "restart", packet=p)
                if s["status"] != "stopped":
                    self._alert(id, "unexpected restart", "warn")
            s.update(
                instance_id=p["inst"],
                instance_started_at=now - p.get("uptime_s", 0) if p["type"] == "hb" else now,
                seq=-1,
            )
        if p["seq"] <= s["seq"]:
            return
        if s["seq"] >= 0:
            s["packet_loss"] += max(0, p["seq"] - s["seq"] - 1)
        s.update(seq=p["seq"], last_seen_at=now)
        if p["type"] == "hb":
            s["interval_s"] = self.registry.get(id, {}).get("interval_s", p["interval_s"])
            self._transition(id, "stopped" if p["state"] == "stopping" else "up")
        elif s["status"] in ("unknown", "late", "down"):
            self._transition(id, "up")
        self._event(
            id,
            p["type"],
            origin="sender",
            state=p.get("state", ""),
            severity=p.get("severity", "warn" if p["type"] == "warn" else ""),
            error_type=p.get("error_type", ""),
            message=p.get("message", ""),
            packet=p,
        )
        if abs(int(now * 1000) - p["ts"]) > 5000 and (id, "skew") not in self._warned:
            self._warned.add((id, "skew"))
            self._event(
                id,
                "warn",
                error_type="clock_skew",
                message="Sender clock differs by more than 5 seconds",
            )
        if p["type"] == "err":
            s["errors"].append(now)
            s.update(
                last_error_at=now, last_error_type=p["error_type"], last_error_message=p["message"]
            )
            self._alert(
                id,
                f"{p['error_type']}:{p.get('fingerprint', '')}",
                "fatal" if p["severity"] == "fatal" else "fail",
                p["message"],
            )
        elif p["type"] == "warn":
            s["warnings"].append(now)
            if sum(t > now - 300 for t in s["warnings"]) >= 10:
                self._alert(id, "10 warnings in 5 minutes", "warn", p["message"])

    def check(self):
        now = self.clock()
        for id in self.registry:
            self.services.setdefault(id, self._new(id))
        for id, s in self.services.items():
            for key, age in (("restarts", 86400), ("warnings", 3600), ("errors", 3600)):
                while s[key] and s[key][0] <= now - age:
                    s[key].popleft()
            if id == "monitor" or (
                s["status"] == "stopped" and "max_silence_s" not in self.registry.get(id, {})
            ):
                continue
            config = self.registry.get(id, {})
            if config.get("profile") and s["last_seen_at"] is None:
                continue
            # Windows are explicit UTC/local wall-clock schedules, including overnight windows.
            window = config.get("active_window")
            if window:
                from zoneinfo import ZoneInfo

                local = datetime.fromtimestamp(now, ZoneInfo(window["timezone"]))
                current = local.strftime("%H:%M")
                start, end = window["start"], window["end"]
                active = (
                    start <= current < end if start < end else current >= start or current < end
                )
                weekday = local.isoweekday()
                if start > end and current < end:
                    weekday = (weekday - 2) % 7 + 1
                if weekday not in window["days"] or not active:
                    s["active_since"] = None
                    continue
                if s.get("active_since") is None:
                    s["active_since"] = now
            last_seen = s["last_seen_at"] if s["last_seen_at"] is not None else self.started
            silence = now - max(last_seen, s.get("active_since") or self.started)
            deadline = config.get("max_silence_s", 3 * s["interval_s"])
            if silence >= deadline:
                self._transition(id, "down")
            elif "max_silence_s" not in config and silence >= 2 * s["interval_s"]:
                self._transition(id, "late")

    def status_rows(self):
        now = self.clock()
        result = []
        for s in self.services.values():
            row = {k: s[k] for k in STATUS_COLUMNS if k in s}
            row.update(
                restarts_24h=len(s["restarts"]),
                warn_count_1h=len(s["warnings"]),
                err_count_1h=len(s["errors"]),
                updated_at=int(now * 1000),
                version=time.time_ns(),
            )
            for key in ("last_seen_at", "instance_started_at", "last_error_at"):
                row[key] = int(s[key] * 1000) if s[key] is not None else None
            result.append(row)
        return result


class Storage:
    """A bounded memory queue and disk spool; ACK only successful event inserts."""

    def __init__(self, monitor, directory, connect=ClickHouse.from_environment):
        self.monitor = monitor
        self.directory = Path(directory) / "spool"
        self.connect = connect
        self.client = None

    def spill(self, events=None):
        if events is None:
            events = self.monitor.events
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            while events:
                batch = list(events)[:500]
                target = self.directory / f"pending-{uuid4()}.jsonl"
                temporary = target.with_suffix(".tmp")
                temporary.write_text(
                    "".join(json.dumps(row) + "\n" for row in batch), encoding="utf-8"
                )
                temporary.replace(target)
                for _ in batch:
                    events.popleft()
        except OSError:
            log.exception("Cannot spill monitoring events")
            while len(events) > 10000:
                events.popleft()

    def _insert(self, table, records, columns):
        assert self.client is not None
        data = []
        for record in records:
            row = []
            for key in columns:
                value = record[key]
                row.append(timestamp(value) if key.endswith("_at") and value is not None else value)
            data.append(row)
        if data:
            self.client.client.insert(table, data, column_names=columns)

    def flush(self, events=None, status=None):
        if events is None:
            events = self.monitor.events
        try:
            if self.client is None:
                self.client = self.connect()
            for path in sorted(self.directory.glob("pending-*.jsonl")):
                batch = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                self._insert("meta.service_events", batch, EVENT_COLUMNS)
                path.unlink()
            while events:
                batch = list(events)[:500]
                self._insert("meta.service_events", batch, EVENT_COLUMNS)
                for _ in batch:
                    events.popleft()
            self._insert(
                "meta.service_status",
                status if status is not None else self.monitor.status_rows(),
                STATUS_COLUMNS,
            )
        except Exception:
            log.exception("Monitoring storage unavailable; spooling events")
            if self.client is not None:
                self.client.close()
            self.client = None
            self.spill(events)
        return events


class Receiver(asyncio.DatagramProtocol):
    def __init__(self, monitor):
        self.monitor = monitor

    def datagram_received(self, data, addr):
        self.monitor.receive(data)


async def run(registry_path, log_dir, port=9125, health_port=9126):
    registry = (
        yaml.safe_load(await asyncio.to_thread(Path(registry_path).read_text, encoding="utf-8"))
        or {}
    )
    loop = asyncio.get_running_loop()
    # notify backends may perform blocking I/O. Queue work off the UDP event loop.
    alerts = asyncio.Queue(maxsize=1000)

    def alert(*args, **kwargs):
        try:
            alerts.put_nowait((args, kwargs))
        except asyncio.QueueFull:
            log.warning("Notification queue full")

    async def notifications():
        while True:
            args, kwargs = await alerts.get()
            try:
                await asyncio.to_thread(notify, *args, **kwargs)
            except Exception:
                log.exception("Notification failed")
            finally:
                alerts.task_done()

    monitor = Monitor(registry, alert=alert)
    storage = Storage(monitor, log_dir)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: Receiver(monitor),
        local_addr=("0.0.0.0", port),  # noqa: S104 - private backend network
    )
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async def health(reader, writer):
        try:
            await asyncio.wait_for(reader.read(2048), 2)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(health, "127.0.0.1", health_port)
    notification_task = asyncio.create_task(notifications())
    self_state = monitor._new("monitor")
    self_state.update(instance_id=str(uuid4()), instance_started_at=time.time(), status="up")
    monitor.services["monitor"] = self_state
    last_flush = 0
    flush_task = None
    try:
        while not stop.is_set():
            monitor.check()
            if flush_task is not None and flush_task.done():
                remaining = flush_task.result()
                monitor.events.extendleft(reversed(remaining))
                flush_task = None
            if flush_task is None and (
                time.monotonic() - last_flush >= 5 or len(monitor.events) >= 500
            ):
                self_state["last_seen_at"] = time.time()
                # Snapshot on the event loop. The worker owns only the detached batch.
                batch, monitor.events = monitor.events, deque(maxlen=10000)
                status = monitor.status_rows()
                flush_task = asyncio.create_task(asyncio.to_thread(storage.flush, batch, status))
                last_flush = time.monotonic()
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass
    finally:
        transport.close()
        server.close()
        await server.wait_closed()
        if flush_task is not None:
            monitor.events.extendleft(reversed(await flush_task))
        self_state["status"] = "stopped"
        self_state["last_seen_at"] = time.time()
        await asyncio.to_thread(storage.flush)
        await alerts.join()
        notification_task.cancel()
