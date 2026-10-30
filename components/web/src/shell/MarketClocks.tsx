import { useEffect, useState } from "react";

interface Exchange { code: string; zone: string; open: number; close: number }

// Regular-session hours in each exchange's local time (minutes after midnight). Holidays are not applied.
const EXCHANGES: Exchange[] = [
  { code: "NY", zone: "America/New_York", open: 9 * 60 + 30, close: 16 * 60 },
  { code: "LDN", zone: "Europe/London", open: 8 * 60, close: 16 * 60 + 30 },
  { code: "MUM", zone: "Asia/Kolkata", open: 9 * 60 + 15, close: 15 * 60 + 30 },
];

function localParts(now: Date, zone: string) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: zone, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(now);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return { weekday: get("weekday"), hour: Number(get("hour")), minute: Number(get("minute")), label: `${get("hour")}:${get("minute")}` };
}

export function sessionOpen(exchange: Exchange, now: Date): boolean {
  const { weekday, hour, minute } = localParts(now, exchange.zone);
  if (weekday === "Sat" || weekday === "Sun") return false;
  const minutes = hour * 60 + minute;
  return minutes >= exchange.open && minutes < exchange.close;
}

export function MarketClocks() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <ul className="term-clocks" aria-label="Exchange clocks (regular sessions, holidays not applied)">
      {EXCHANGES.map((exchange) => {
        const open = sessionOpen(exchange, now);
        return (
          <li key={exchange.code} className={open ? "is-open" : ""} title={`${exchange.code} ${open ? "regular session open" : "closed"}`}>
            <i aria-hidden="true" />
            <span>{exchange.code}</span>
            <time>{localParts(now, exchange.zone).label}</time>
            <span className="sr-only">{open ? "open" : "closed"}</span>
          </li>
        );
      })}
    </ul>
  );
}
