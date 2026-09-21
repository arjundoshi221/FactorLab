"""Read-only validation of the configured EODHD US common-stock master."""

from factorlab.countries.us.equities.eodhd.client import EODHDClient
from factorlab.countries.us.equities.eodhd.us_universe import normalize_master

payload = EODHDClient().get_exchange_symbols("US", instrument_type="common_stock")
master = normalize_master(payload)
venues = sorted({item["exchange_code"] for item in master})
print(f"raw={len(payload)} eligible={len(master)} venues={','.join(venues)}", flush=True)
assert len(master) >= 2_000, "Configured EODHD plan returned too few eligible instruments"
