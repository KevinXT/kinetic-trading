# Provider-agnostic financial data

The financial-data boundary lives in `packages/market_data`. It keeps API response
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

Phase 1 defines the contracts and local persistence foundation. It does not make
network calls or register runnable provider tasks.

## Domain contracts

The public records are frozen dataclasses in `market_data.domain`:

- `PriceBar`: OHLCV data with feed and adjustment identity.
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

The focused protocols are `PriceDataProvider`, `CompanyDataProvider`, and
`MacroDataProvider`. They accept provider-independent request dataclasses and return
normalized records. They intentionally do not form one large provider interface.

`ProviderRegistry` holds separate factories for each category. Registry instances
are explicit and do not share global mutable state. Alpaca, SEC EDGAR, FRED, and
future mock/provider factories will be registered in their implementation phases.
Downstream modules should import domain records or protocols, never concrete clients.

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
`data/processed/financial_data/` by default:

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
incoming payload updates an existing stored key.

Files are sorted and serialized deterministically, written to a temporary file in the
target directory, flushed, and atomically replaced. A malformed existing dataset
fails loudly and is not partially rewritten. Every dataset has a deterministic
`<dataset>.metadata.json` sidecar containing the record type and
`financial-data.v1` schema version. Incompatible metadata is rejected so future
migrations are explicit.

Logical identities are:

- bars: symbol, timestamp, timeframe, provider, feed, adjustment
- filings: accession number
- facts: CIK, taxonomy, concept, unit, period, form, filed date, accession
- macro observations: series, observation date, real-time range, vintage, provider
- instruments: internal instrument ID

`MarketEvent` is a contract only in Phase 1. Event conversion, persistence, and
event-reaction infrastructure remain deferred to Phase 6.

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

Provider cache-key payloads must contain the provider, endpoint, normalized request,
schema version, and relevant feed/adjustment or vintage/real-time options. They must
never contain credentials or authorization headers.

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

The project does not load `.env` files. Provider phases will read these values from
the process environment and validate them before network calls. Never put real
values in YAML, committed files, logs, snapshots, cache keys, or run artifacts.

## Planned provider phases

1. Alpaca historical bars with pagination, retries, rate-limit handling, caching,
   fixture tests, and an independent pipeline task.
2. SEC ticker/CIK mapping, recent filing metadata, and company facts with a
   conservative limiter and independent tasks.
3. FRED observations with missing-value and vintage-aware normalization.
4. SEC/GDELT event adapters and basic point-in-time event-reaction research.

Each provider pipeline will remain independently rerunnable because update schedules
and failure modes differ.

## Data limitations

Provider free tiers can restrict history, feeds, request rates, or revision access.
Feed and adjustment modes must not be silently mixed. Filing dates are not a
substitute for SEC acceptance times, and revised macro data must not enter an earlier
as-of analysis. Missing bars and exchange calendars require explicit handling in
event studies.

Kinetic Trading currently supports research and paper analysis. It does not execute
live trades, provide investment advice, guarantee predictions, or establish that an
event caused a market move.
