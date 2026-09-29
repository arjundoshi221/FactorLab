"""A stateful stand-in for clickhouse-connect used by the sink conformance suite.

It keeps the latest version of each keyed row (``FINAL`` semantics) and
answers exactly the queries ``ClickHouseSinks``/``V2ReferenceWriter`` issue.
An unexpected query fails loudly so the fake cannot silently drift.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

KEYS = {
    "ref.entities": ("entity_id",),
    "ref.securities": ("security_id",),
    "ref.listings": ("listing_id",),
    "ref.contracts": ("contract_id",),
    "ref.identifier_aliases": ("alias_kind", "alias_value", "target_kind", "target_id"),
    "meta.unresolved_entities": ("source", "alias_kind", "alias_value"),
    "ref.universes": ("universe_id",),
    "ref.universe_membership": ("universe_id", "effective_from", "listing_id"),
    "ref.legislator_terms": ("bioguide_id", "term_start"),
    "alt.political_committees": ("committee_id", "effective_from"),
    "alt.political_committee_memberships": ("country_code", "congress_number", "committee_id",
                                            "bioguide_id", "effective_from"),
    "alt.political_filings": ("country_code", "chamber", "filing_id"),
    "alt.political_trades": ("political_trade_id",),
}


class Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class FakeClickHouse:
    def __init__(self, exchanges: dict[str, tuple[str, str, str]] | None = None) -> None:
        self.exchanges = exchanges or {"NSE": ("XNSE", "IN", "INR"), "XNAS": ("XNAS", "US", "USD")}
        self.currencies = {currency for _, _, currency in self.exchanges.values()}
        self.tables: dict[str, dict[tuple, dict[str, Any]]] = defaultdict(dict)
        self.log: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.queries: list[str] = []

    # -- writes --------------------------------------------------------------
    def insert(self, table, rows, column_names):
        for row in rows:
            record = dict(zip(column_names, row, strict=True))
            self.log[table].append(record)
            if table in KEYS:
                self.tables[table][tuple(record[k] for k in KEYS[table])] = record

    def close(self):
        pass

    # -- helpers -------------------------------------------------------------
    def rows(self, table: str) -> list[dict[str, Any]]:
        return list(self.tables[table].values()) if table in KEYS else list(self.log[table])

    def _listing(self, listing_id):
        return self.tables["ref.listings"].get((listing_id,))

    def _security(self, security_id):
        return self.tables["ref.securities"].get((security_id,))

    @staticmethod
    def _scoped(listing, p):
        if "exchange" in p:
            return listing["exchange_code"] == p["exchange"]
        return listing["country_code"] == p["country"]

    def _aliases(self, kind, target_kind):
        return [a for a in self.rows("ref.identifier_aliases")
                if a["alias_kind"] == kind and a["target_kind"] == target_kind
                and a["valid_to"] is None]

    # -- reads ---------------------------------------------------------------
    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> Result:
        p = parameters or {}
        self.queries.append(sql)
        if "FROM ref.exchanges FINAL" in sql:
            row = self.exchanges.get(p["exchange"])
            return Result([row] if row else [])
        if "FROM ref.currencies FINAL" in sql:
            return Result([(p["currency"],)] if p["currency"] in self.currencies else [])
        if sql.startswith("SELECT first_seen, last_seen, legal_name FROM ref.entities"):
            row = self.tables["ref.entities"].get((p["id"],))
            return Result([(row["first_seen"], row["last_seen"], row["legal_name"])] if row else [])
        if sql.startswith("SELECT issue_date, isin"):
            row = self._security(p["id"])
            return Result([(row["issue_date"], row["isin"], row["cusip"], row["figi"],
                            row["share_class"], row["sector_id"], row["sector_classification"])]
                          if row else [])
        if sql.startswith("SELECT first_traded, local_symbol, is_primary"):
            row = self._listing(p["id"])
            return Result([(row["first_traded"], row["local_symbol"], row["is_primary"])]
                          if row else [])
        if "SELECT minOrNull(valid_from) FROM ref.identifier_aliases" in sql:
            kind = p.get("kind")
            target_kind = p.get("target_kind") or (
                "contract" if "target_kind = 'contract'" in sql else "listing")
            dates = [a["valid_from"] for a in self._aliases(kind, target_kind)
                     if a["alias_value"] == p["value"] and a["target_id"] == p["id"]]
            return Result([(min(dates) if dates else None,)])
        if sql.startswith("SELECT exchange_code, country_code FROM ref.listings FINAL"):
            row = self._listing(p["listing_id"])
            return Result([(row["exchange_code"], row["country_code"])] if row else [])
        if sql.startswith("SELECT alias_value, target_id, valid_from FROM ref.identifier_aliases"):
            values = set(p["values"])
            return Result([(a["alias_value"], a["target_id"], a["valid_from"])
                           for a in self._aliases(p["kind"], p["target"])
                           if a["alias_value"] in values])
        if "WHERE s.isin = {isin:String}" in sql:
            return Result([(listing["listing_id"],) for listing in self.rows("ref.listings")
                           if self._scoped(listing, p) and listing["active"]
                           and (self._security(listing["security_id"]) or {}).get("isin") == p["isin"]])
        if "AND l.trading_symbol = {symbol:String}" in sql:
            return Result([(listing["listing_id"], (self._security(listing["security_id"]) or {}).get("isin"))
                           for listing in self.rows("ref.listings")
                           if self._scoped(listing, p) and listing["active"]
                           and listing["trading_symbol"] == p["symbol"]])
        if sql.startswith("SELECT l.listing_id, l.security_id, s.entity_id"):
            out = []
            for listing_id in p["ids"]:
                listing = self._listing(listing_id)
                s = self._security(listing["security_id"]) if listing else None
                if listing and s:
                    out.append((listing["listing_id"], listing["security_id"], s["entity_id"],
                                s["security_type"], listing["country_code"]))
            return Result(out)
        if sql.startswith("SELECT listing_id FROM ref.listings FINAL WHERE exchange_code"):
            return Result([(listing["listing_id"],) for listing in self.rows("ref.listings")
                           if listing["exchange_code"] == p["exchange"] and listing["active"]])
        if sql.startswith("SELECT l.listing_id, l.exchange_code, l.trading_symbol"):
            out = []
            for listing_id in p["ids"]:
                listing = self._listing(listing_id)
                s = self._security(listing["security_id"]) if listing else None
                if listing and s:
                    out.append((listing["listing_id"], listing["exchange_code"], listing["trading_symbol"],
                                listing["country_code"], s["isin"]))
            return Result(out)
        if sql.startswith("SELECT contract_id, underlying_listing_id, country_code"):
            return Result([(c["contract_id"], c["underlying_listing_id"], c["country_code"])
                           for c in self.rows("ref.contracts") if c["contract_id"] in p["ids"]])
        if "AND contract_type = 'future'" in sql:
            return Result([(c["contract_id"],) for c in self.rows("ref.contracts")
                           if c["underlying_listing_id"] == p["listing"]
                           and c["expiry"] == p["expiry"] and c["contract_type"] == "future"
                           and c["strike"] is None])
        if sql.startswith("SELECT a.target_id, a.alias_value"):
            out = []
            for a in self._aliases(p["kind"], "listing"):
                if a["target_id"] not in p["ids"]:
                    continue
                listing = self._listing(a["target_id"])
                s = self._security(listing["security_id"]) if listing else None
                if listing and s:
                    out.append((a["target_id"], a["alias_value"], a["valid_from"],
                                listing["exchange_code"], listing["trading_symbol"], listing["country_code"],
                                s["isin"]))
            return Result(out)
        if "FROM ref.universe_membership FINAL WHERE universe_id = {universe:String}" in sql:
            return Result([(m["listing_id"], m["effective_from"])
                           for m in self.rows("ref.universe_membership")
                           if m["universe_id"] == p["universe"] and m["effective_to"] is None])
        if "FROM ref.universes FINAL WHERE universe_id" in sql:
            return Result([(int((p["universe"],) in self.tables["ref.universes"]),)])
        if sql.startswith("SELECT DISTINCT listing_id FROM ref.universe_membership"):
            day = p["day"]
            return Result(sorted({(m["listing_id"],) for m in self.rows("ref.universe_membership")
                                  if m["universe_id"] in p["codes"]
                                  and m["effective_from"] <= day
                                  and (m["effective_to"] is None or m["effective_to"] >= day)},
                                 key=str))
        if sql.startswith("SELECT entity_id FROM ref.entities FINAL WHERE entity_id IN"):
            return Result([(e["entity_id"],) for e in self.rows("ref.entities")
                           if e["entity_id"] in p["ids"]])
        if sql.startswith("SELECT alias_value, target_id FROM ref.identifier_aliases FINAL"):
            return Result([(a["alias_value"], a["target_id"])
                           for a in self._aliases(p["kind"], "entity")
                           if a["alias_value"] in p["values"]])
        if sql.startswith("SELECT target_id, alias_value FROM ref.identifier_aliases FINAL"):
            return Result([(a["target_id"], a["alias_value"])
                           for a in self._aliases("bioguide", "entity") if a["target_id"] in p["ids"]])
        if "FROM alt.political_filings FINAL WHERE country_code = {country:String} AND chamber" \
                in sql and "filing_id IN" in sql:
            from factorlab.storage.sinks.political import FILING_COLUMNS
            return Result([tuple(f[c] for c in FILING_COLUMNS)
                           for f in self.rows("alt.political_filings")
                           if f["country_code"] == p["country"] and f["chamber"] == p["chamber"]
                           and f["filing_id"] in p["ids"]])
        if sql.startswith("SELECT filing_id, filing_year, filing_url FROM alt.political_filings"):
            found = sorted((f for f in self.rows("alt.political_filings")
                            if f["country_code"] == p["country"] and f["chamber"] == p["chamber"]),
                           key=lambda f: (f["filing_date"], f["filing_id"]), reverse=True)
            return Result([(f["filing_id"], f["filing_year"], f["filing_url"])
                           for f in found[:p["limit"]]])
        if sql.startswith("SELECT DISTINCT l.listing_id, l.security_id, s.entity_id"):
            out = []
            for listing in self.rows("ref.listings"):
                s_ = self._security(listing["security_id"])
                if (s_ and listing["trading_symbol"] == p["ticker"] and listing["country_code"] == p["country"]
                        and (listing["first_traded"] is None or listing["first_traded"] <= p["day"])
                        and (listing["last_traded"] is None or listing["last_traded"] >= p["day"])):
                    out.append((listing["listing_id"], listing["security_id"], s_["entity_id"],
                                s_["security_type"]))
            return Result(out)
        if "max(bar_time)" in sql:
            table = "market.bars" if "FROM market.bars " in sql else "market.futures_contract_bars"
            key = "listing_id" if table == "market.bars" else "contract_id"
            marks: dict[uuid.UUID, Any] = {}
            for bar in self.log[table]:
                if (bar["resolution"] == p["resolution"] and bar["source"] == p["source"]
                        and bar[key] in p["ids"]):
                    marks[bar[key]] = max(marks.get(bar[key], bar["bar_time"]), bar["bar_time"])
            return Result(list(marks.items()))
        if "FROM raw.archive WHERE raw_id" in sql:
            for row in self.log["raw.archive"]:
                if row["raw_id"] == p["id"]:
                    return Result([(row["source"], row["source_channel"], row["transport"],
                                    row["source_url"], row["request_key"], row["status_code"],
                                    row["response_headers"], row["response_body"],
                                    row["content_type"], row["content_encoding"],
                                    row["fetched_at"], row["metadata_json"])])
            return Result([])
        raise AssertionError(f"FakeClickHouse does not understand: {sql}")
