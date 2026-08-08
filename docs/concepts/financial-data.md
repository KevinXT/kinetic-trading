# Provider-agnostic financial data

The financial-data boundary lives in `kinetic.ingestion.market`. It keeps API response
formats and credentials below the provider layer:

```text
provider client
  -> raw response cache
  -> provider normalizer
  -> domain record
  -> FinancialDataStore
  -> feature or research pipeline
  -> strategy, dashboard, or analysis
```

Providers fetch data. Normalizers translate it. Stores persist it. Feature pipelines
combine it. Strategies and dashboards consume normalized records.

The domain contracts, local JSONL store, provider registry, and the first complete
provider path (Alpaca historical US stock bars) are implemented. SEC EDGAR, FRED,
and event-reaction infrastructure remain separate follow-on work on the same
contracts.

## Domain contracts

The public records are frozen dataclasses in `kinetic.data.schemas`:

- `PriceBar`: OHLCV data with feed, adjustment, and currency identity. OHLC values
  must be finite and non-negative, `high >= low`, and open/close must lie within
  `[low, high]`. Volume and trade count are non-negative integers.
- `FilingEvent`: SEC filing metadata keyed by accession number.
- `FinancialFact`: one reported fact, preserving taxonomy, concept, unit, period,
  form, filing date, and accession.
- `MacroObservation`: one observation with real-time and optional vintage dates.
- `MarketEvent`: a common event envelope for future SEC, GDELT, and macro events.
- `Instrument`: stable internal identity plus ticker, CIK, aliases, classification,
  and validity dates.

All datetimes must be timezone-aware. Records canonicalize them to UTC and serialize
them with a `Z` suffix. Symbols are uppercased and CIKs are zero-padded. Original SEC
taxonomy, concept, unit, form, accession, and provider strings are otherwise
preserved rather than speculatively remapped. Naive datetimes are rejected.

Three times must not be conflated:

```text
period_end   = the period described by a fact
effective_at = when information became publicly knowable
observed_at  = when Kinetic retrieved or observed it
```

Ingestion preserves every SEC filing/fact revision and every FRED vintage. Selecting
the latest value or constructing an as-of view belongs in a query or feature layer.

## Provider interfaces and registry

The implemented protocol today is `PriceDataProvider`. It accepts provider-independent
request dataclasses and returns normalized `PriceBar` records. Company/macro provider
protocols are deferred until SEC EDGAR / FRED integrations exist.

`ProviderRegistry` holds price-provider factories only. Registry instances are
explicit and do not share global mutable state. Alpaca is registered as the
`alpaca` price provider.

Downstream modules should import domain records or protocols, never concrete clients.

## Alpaca historical stock bars

`alpaca_historical_bars` uses the existing provider-blind YAML runner and the
multi-symbol endpoint at `https://data.alpaca.markets/v2/stocks/bars`. The client,
raw validation, cache adapter, and normalizer are isolated under
`kinetic.ingestion.market.alpaca`. Code outside that namespace consumes `PriceBar`.

Supported request fields are symbols, timeframe, explicit RFC 3339 start/end,
feed, adjustment, limit, sort, optional `asof`, and optional currency. A task may
also resolve `lookback_days` once at startup. Committed examples use explicit dates
so cache keys and artifacts remain reproducible.

Optional `currency` is an ISO-4217 code forwarded to Alpaca as the requested price
denomination for the stock bars endpoint (Alpaca documents default `USD`). When
omitted, normalized bars store `USD`. Kinetic treats currency as request and
identity metadata; it does not model exchange rates or claim a particular
conversion method for non-USD responses. Currency is part of `PriceBar` identity
and storage keys so distinct denominations cannot collide.

Feed and adjustment are required. The example uses `feed: iex`, which is suitable
for many free-tier accounts but is not the complete consolidated SIP market feed.
The client never silently falls back between feeds. SIP entitlement or recency
errors identify the requested feed without exposing credentials.

Pagination continues through every returned token, including pages that contain
only one of several requested symbols. Repeated tokens and the configured maximum
page count fail explicitly. Retryable transport failures, HTTP 429, and selected
5xx responses use bounded retries; ordinary 4xx failures are not retried.

Raw pages are cached as one complete logical result, preserving page boundaries.
Cache keys include the normalized non-secret `api_origin`, every response-affecting
request field (including currency, defaulting omitted requests to USD), and schema
version `alpaca-bars-v2`. They never contain credentials. Equivalent base URL
spellings (trailing slash, host/scheme case, default ports) collapse to one origin.
Query strings and URL credentials are rejected. Older `alpaca-bars-v1` cache files
are left orphaned intentionally. Cache summaries distinguish hit, miss, expired, and
forced refresh and report whether a cache file was written.

Run:

```bash
export ALPACA_API_KEY_ID="..."
export ALPACA_API_SECRET_KEY="..."
kinetic run configs/alpaca_daily_bars.yaml
```

The run writes:

```text
alpaca_request.json
alpaca_cache_summary.json
alpaca_pages_summary.json
normalized_price_bars.jsonl
alpaca_validation_summary.json
alpaca_store_summary.json
alpaca_summary.json
```

Normalized bars are also placed in `ctx.state["price_bars"]` and upserted through
`FinancialDataStore` into `warehouse/normalized/market/market_bars.jsonl` for the example.
No order, account, position, WebSocket, quote, trade, option, crypto, or news API is
implemented.

## Instrument identity

`instrument_id` is the permanent identity; ticker is an attribute that can change.
`InMemoryInstrumentResolver` supports a curated first-version instrument master and
resolves internal IDs, symbols, zero-padded CIKs, company names, and aliases while
respecting validity dates. Ambiguous aliases raise an error rather than guessing.
The explicit `AmbiguousInstrumentError` includes every matching internal instrument
ID. Exact internal ID, symbol, and CIK indexes take precedence over alias matching.

Full global entity resolution is out of scope. A later GDELT adapter can resolve
company mentions through the same interface without importing a financial provider.

## Local persistence

`JsonlFinancialDataStore` writes independent datasets below
`warehouse/normalized/financial_data/` by default:

```text
market_bars.jsonl
sec_filings.jsonl
financial_facts.jsonl
macro_observations.jsonl
instrument_master.jsonl
```

Upserts return inserted, updated, skipped, and total counts. Identical reruns are
idempotent. Identical duplicates within one incoming batch are collapsed and counted
as skipped. Conflicting payloads with the same batch key raise
`ConflictingBatchRecordsError`; input ordering never decides the winner. A changed
incoming market payload updates an existing stored key. `retrieved_at` is first-seen
provenance: it is retained on skip and excluded from semantic equality, so
force-refreshing unchanged OHLCV data does not count as a correction. Legacy bar rows
without `currency` are keyed as `USD` without rewriting the file on read; a real
payload update rewrites the row in the current schema (including `currency`).

Files are sorted and serialized deterministically, written to a temporary file in the
target directory, flushed, and atomically replaced. A malformed existing dataset
fails loudly and is not partially rewritten. Every dataset has a deterministic
`<dataset>.metadata.json` sidecar containing the record type and
`financial-data.v1` schema version. Incompatible metadata is rejected so future
migrations are explicit.

Logical identities are:

- bars: symbol, timestamp, timeframe, provider, feed, adjustment, currency
  (legacy rows missing `currency` default to `USD` for key compatibility)
- filings: accession number
- facts: CIK, taxonomy, concept, unit, period, form, filed date, accession
- macro observations: series, observation date, real-time range, vintage, provider
- instruments: internal instrument ID

`MarketEvent` is a domain contract only. Event conversion, persistence, and
event-reaction infrastructure remain deferred.

### Concurrency and scale

The local store enforces one writer per dataset using an atomically created
`<dataset>.lock` file. A second writer immediately receives
`ConcurrentDatasetWriteError`; writers do not wait or silently overwrite one another.
The lock is removed in a `finally` block, including write failures. A process killed
without cleanup can leave a stale lock that must be inspected and removed manually.

JSONL upserts read the complete dataset into memory and rewrite the complete file.
This is appropriate for local development and modest curated datasets, not
high-volume concurrent ingestion. It does not provide transactions across multiple
datasets, automatic stale-lock recovery, or distributed/network-filesystem locking.
A future BigQuery or database store should implement the same protocol for larger
workloads.

The `FinancialDataStore` protocol uses normalized records and can be implemented by
BigQuery later. Existing BigQuery execution must continue through
`SafeBigQueryClient`, including dry runs, partition filters, query-size checks, and
cost guardrails. Local development does not require BigQuery.

## Cache behavior

Financial providers will reuse `common.cache`. `CachePolicy` adds optional TTL,
force-refresh, and schema-version invalidation while preserving the existing GDELT
cache call signatures, return behavior, and file format. `get_or_fetch_json_result`
is the opt-in API for hit/miss status metadata; existing callers can continue using
`get_or_fetch_json` unchanged. Cache writes are atomic.

Provider cache-key payloads must contain the provider, endpoint, normalized API
origin when applicable, normalized request, schema version, and relevant
feed/adjustment or vintage/real-time options. They must never contain credentials
or authorization headers.

Expected policy choices in later phases:

- completed historical bars and explicit historical vintages: effectively immutable
- current-day bars and recent SEC submissions: short TTL
- SEC company mapping and facts: several-hour or daily TTL
- latest FRED observations: several-hour or daily TTL

## Environment variables

Copy `.env.example` only as a reference for variable names:

```text
ALPACA_API_KEY_ID
ALPACA_API_SECRET_KEY
SEC_USER_AGENT
FRED_API_KEY
```

The project does not load `.env` files. Alpaca reads its two credentials from the
process environment and validates them before network calls. Never put real
values in YAML, committed files, logs, snapshots, cache keys, or run artifacts.

## Planned provider phases

1. SEC ticker/CIK mapping, recent filing metadata, and company facts with a
   conservative limiter and independent tasks.
2. FRED observations with missing-value and vintage-aware normalization.
3. SEC/GDELT event adapters and basic point-in-time event-reaction research.

Each provider pipeline will remain independently rerunnable because update schedules
and failure modes differ.

## Data limitations

Provider free tiers can restrict history, feeds, request rates, or revision access.
Feed and adjustment modes must not be silently mixed. Filing dates are not a
substitute for SEC acceptance times, and revised macro data must not enter an earlier
as-of analysis. Missing bars and exchange calendars require explicit handling in
event studies.

Default tests use sanitized fixtures and fake transports. The opt-in live smoke test
requires `RUN_PROVIDER_INTEGRATION_TESTS=1` plus both Alpaca credential variables.
Provider responses can be incomplete because of market sessions, symbol history,
entitlements, feed coverage, or provider outages; successful ingestion is not a
guarantee of complete market data.

Kinetic Trading currently supports research and paper analysis. It does not execute
live trades, provide investment advice, guarantee predictions, or establish that an
event caused a market move.
