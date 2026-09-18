# Security Policy

## Threat model

FactorLab is a single-user research platform that aggregates **public**
financial-disclosure and market data from external vendors. It is not
internet-facing and has no multi-tenant requirements. The threats we defend
against are:

1. **Accidental credential leakage** — API keys, tokens, or DB connection
   strings ending up in git, logs, or the audit table.
2. **Path-traversal via upstream-supplied identifiers** — a poisoned `doc_id`
   in a House Clerk year-XML index escaping the cache root.
3. **XML parser DoS** — billion-laughs / quadratic-blowup attacks via crafted
   XML in upstream responses (mostly theoretical with trusted .gov sources,
   but defended in transit).
4. **Zip-slip / zipbomb** — a poisoned annual filing ZIP from disclosures.house.gov
   that decompresses to gigabytes or writes outside the extraction dir.
5. **SQL injection via dynamic identifiers** — table names interpolated from
   `information_schema` queries (low real-world risk; defended by whitelist).

What we DON'T defend against (out of scope):

- Multi-user authorization (single-user, all reads)
- DDoS / availability
- Side-channels in the host OS / IDE
- Third-party vendor breach (we accept their security posture by relying on them)

## Defenses in place

- **URL secret scrubbing.** Every URL written to logs or `audit.raw_archive`
  passes through `_security.redact_url`, which replaces `api_key=…`,
  `token=…`, `secret=…`, `access_token=…`, etc. with `=REDACTED` before
  persistence.
- **Path validation.** `_client.py` calls `_security.assert_under_root` on
  every resolved cache path; any path that resolves outside `DATA_ROOT`
  raises immediately.
- **Defused XML.** House Clerk year-XML index uses `defusedxml.ElementTree`
  instead of stdlib `xml.etree`. Blocks billion-laughs and external-entity
  attacks.
- **ZIP entry guard.** Year-ZIP extraction rejects entries with `..` or
  absolute paths and caps decompressed size at 200 MB.
- **Pre-commit hooks.** Block `data/`, `logs/`, `.env*` paths and any
  unredacted `api_key=<long-value>` patterns from being committed. See
  `CONTRIBUTING.md` for setup.
- **Dependency posture.** `requests>=2.32.3` (CVE-2024-35195 fixed),
  `defusedxml>=0.7.1`. Other deps audited 2026-05-01 with no open advisories.
- **`audit.raw_archive` retention.** Probe / smoke-test endpoints (`lda_probe`)
  are deactivated; their historical rows were purged in migration 017.

## Reporting a vulnerability

Single-user repo; report directly to the project owner.

## Rotating credentials

If a key is suspected exposed:

1. Revoke the key at the vendor's developer portal (Finnhub / FEC / Congress.gov /
   Schwab / EODHD / Upstox / IBKR — depending on which leaked).
2. Issue a new key.
3. Update `.env` (DO NOT commit the new value).
4. If the historical leak appeared in `audit.raw_archive`, run:
   ```sql
   UPDATE audit.raw_archive
   SET source_url = regexp_replace(
       source_url, '(api_key|token|secret|access_token)=[^&]+', '\1=REDACTED', 'gi'
   );
   ```
5. Force-pull `.env` from authoritative source (it's gitignored, so check
   1Password / your secret manager).
