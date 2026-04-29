"""Seed reference data: countries, currencies, fx_pairs, markets, exchanges.

Revision ID: 006
Revises: 005
Create Date: 2026-04-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- ref.countries --
    op.execute("""
        INSERT INTO ref.countries (code, name, region, timezone) VALUES
            ('IN', 'India',          'asia',     'Asia/Kolkata'),
            ('US', 'United States',  'americas', 'America/New_York'),
            ('SG', 'Singapore',      'asia',     'Asia/Singapore'),
            ('GB', 'United Kingdom', 'europe',   'Europe/London'),
            ('DE', 'Germany',        'europe',   'Europe/Berlin')
        ON CONFLICT (code) DO NOTHING
    """)

    # -- ref.currencies --
    op.execute("""
        INSERT INTO ref.currencies (code, name, symbol, country_code) VALUES
            ('INR', 'Indian Rupee',      'INR', 'IN'),
            ('USD', 'US Dollar',         'USD', 'US'),
            ('SGD', 'Singapore Dollar',  'SGD', 'SG'),
            ('GBP', 'British Pound',     'GBP', 'GB'),
            ('EUR', 'Euro',              'EUR', 'DE')
        ON CONFLICT (code) DO NOTHING
    """)

    # -- ref.fx_pairs --
    op.execute("""
        INSERT INTO ref.fx_pairs (base, quote, pair_code, source) VALUES
            ('USD', 'INR', 'USDINR', 'ecb'),
            ('INR', 'USD', 'INRUSD', 'ecb'),
            ('USD', 'SGD', 'USDSGD', 'ecb'),
            ('SGD', 'USD', 'SGDUSD', 'ecb'),
            ('USD', 'GBP', 'USDGBP', 'ecb'),
            ('GBP', 'USD', 'GBPUSD', 'ecb'),
            ('USD', 'EUR', 'USDEUR', 'ecb'),
            ('EUR', 'USD', 'EURUSD', 'ecb'),
            ('INR', 'SGD', 'INRSGD', 'ecb'),
            ('SGD', 'INR', 'SGDINR', 'ecb')
        ON CONFLICT (pair_code) DO NOTHING
    """)

    # -- ref.markets --
    op.execute("""
        INSERT INTO ref.markets (code, name, country_code, currency_code) VALUES
            ('IND', 'India Equities', 'IN', 'INR'),
            ('USA', 'US Equities',    'US', 'USD')
        ON CONFLICT (code) DO NOTHING
    """)

    # -- ref.exchanges --
    op.execute("""
        INSERT INTO ref.exchanges (code, name, market_code, country_code, currency_code, timezone, open_time, close_time, calendar_key) VALUES
            ('NSE',    'National Stock Exchange',  'IND', 'IN', 'INR', 'Asia/Kolkata',      '09:15:00', '15:30:00', 'XBOM'),
            ('BSE',    'Bombay Stock Exchange',    'IND', 'IN', 'INR', 'Asia/Kolkata',      '09:15:00', '15:30:00', 'XBOM'),
            ('NYSE',   'New York Stock Exchange',  'USA', 'US', 'USD', 'America/New_York',  '09:30:00', '16:00:00', 'XNYS'),
            ('NASDAQ', 'NASDAQ',                   'USA', 'US', 'USD', 'America/New_York',  '09:30:00', '16:00:00', 'XNAS')
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM ref.exchanges WHERE code IN ('NSE', 'BSE', 'NYSE', 'NASDAQ')")
    op.execute("DELETE FROM ref.markets WHERE code IN ('IND', 'USA')")
    op.execute("DELETE FROM ref.fx_pairs WHERE pair_code IN ('USDINR','INRUSD','USDSGD','SGDUSD','USDGBP','GBPUSD','USDEUR','EURUSD','INRSGD','SGDINR')")
    op.execute("DELETE FROM ref.currencies WHERE code IN ('INR', 'USD', 'SGD', 'GBP', 'EUR')")
    op.execute("DELETE FROM ref.countries WHERE code IN ('IN', 'US', 'SG', 'GB', 'DE')")
