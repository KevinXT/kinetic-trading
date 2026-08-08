# Adding a data provider

This walks through exactly which files you would add to integrate a new provider,
using **FRED** (Federal Reserve Economic Data) as the worked example.

**FRED is not implemented.** Nothing in this document describes existing code
except the two providers it points at as templates. It exists so that when
someone does implement it, the shape is not a fresh decision.

## The shape

Every provider integration is the same six things:

1. a **raw model** — the provider's payload, as it actually arrives
2. a **client** — HTTP, auth, retries, pagination, rate limits
3. a **config** — how the YAML `providers:` block maps to client settings
4. a **normalizer** — raw model → canonical `kinetic.data` schema
5. a **task** — the pipeline step that ties them together
6. **fixtures and tests** — recorded or synthetic payloads, no live calls

Two implementations already follow it. Read one before starting:

- `src/kinetic/ingestion/market/alpaca/` — HTTP + pagination + caching + storage
- `src/kinetic/ingestion/news/gdelt/` — HTTP + query building + parsing

## Files you would add for FRED

```
src/kinetic/ingestion/macro/
├── __init__.py
└── fred/
    ├── __init__.py          public surface: client, provider, normalizer
    ├── raw_models.py        FredObservation, FredSeriesResponse — FRED's shapes
    ├── client.py            HTTP, api_key handling, retries, pagination
    ├── config.py            providers.macro.fred -> FredProviderConfig
    ├── errors.py             FredAuthenticationError, FredInvalidRequestError, ...
    ├── normalize.py         FredObservation -> kinetic.data.schemas.macro.MacroObservation
    └── tasks.py             fred_series_task(ctx, params)
```

```
tests/
├── fixtures/fred/
│   ├── one_page.json                recorded or synthetic FRED responses
│   ├── two_page_first.json
│   ├── two_page_second.json
│   ├── revised_observation.json     a vintage revision — see "Point in time"
│   └── http_errors.json
├── unit/ingestion/
│   ├── test_fred_client.py          retries, pagination, error mapping
│   ├── test_fred_config.py          config parsing and validation
│   └── test_fred_normalize.py       raw -> canonical, field by field
└── integration/
    └── test_fred_task.py            the task end to end against fixtures
```

## Files you would change

**`src/kinetic/bootstrap.py`** — one line, in the composition root:

```python
from kinetic.ingestion.macro.fred.tasks import fred_series_task
...
registry.register("macro.fred.fetch_series", fred_series_task)
```

Nothing else registers it. There is no plugin discovery, no entry point, no
import side effect. If the task is not in this file, the platform does not have
it — which means `kinetic task list` is always the truth.

**`configs/providers/fred.yaml`** (new) — a provider-only fragment configs can
include:

```yaml
providers:
  macro:
    fred:
      base_url: "https://api.stlouisfed.org/fred"
      api_key_env: "FRED_API_KEY"     # the name of the env var, never the key
      timeout_seconds: 30
      maximum_attempts: 4
```

**`pyproject.toml`** — only if FRED needs a dependency the platform does not
already have. It almost certainly does not: `requests` is already there, and both
existing providers use it rather than a vendor SDK.

**`README.md`** — move FRED out of "What has not been built".

## Files you would *not* touch

- `kinetic/data/schemas/macro.py` — `MacroObservation` already exists and is
  provider-independent. If FRED needs a field it does not have, add the field to
  the canonical schema deliberately, as a canonical concept. Do not add
  `fred_realtime_start`.
- `kinetic/core/**` — a new provider is not a change to the pipeline runtime.
- `kinetic/processing/**` — unless FRED data needs a genuinely new deterministic
  transformation, in which case it goes in `processing/macro/`, not in the
  adapter.

## Point in time — the part that is easy to get wrong

FRED is the canonical example of why this platform separates timestamps. A macro
series has at least three, and conflating them silently destroys backtest
validity:

| Timestamp | Meaning | `MacroObservation` field |
| --- | --- | --- |
| observation date | the period the number describes (e.g. "March 2026 CPI") | `observation_date` |
| release time | when that number first became public | `release_datetime` |
| vintage | *which revision* of the number this is | `vintage` |

FRED's ALFRED interface exposes vintages explicitly through
`realtime_start` / `realtime_end`. A normalizer that drops them and keeps only
the latest value produces a series that a 2024 backtest could not have seen —
the single most common way a macro backtest lies to you.

The rules from [data-lifecycle.md](../architecture/data-lifecycle.md) apply
without exception:

- preserve the raw payload under `warehouse/raw/`; never overwrite it
- if FRED does not supply a timestamp, emit `None` with a capability flag — never
  fabricate one, and never substitute the observation date for the release date
- a revision is a **new record**, not an update to an existing one

## Credentials

The config stores the *name* of an environment variable, never a value:
`api_key_env: "FRED_API_KEY"`. Add it to `.env.example` with an empty value.
Unit tests must never need a real key — that is what the fixtures are for.

## Cost

FRED is free, so no cost policy applies. If you add a **billable** provider,
route it through `kinetic.ingestion.cost` the way the BigQuery client does:
estimate before executing, cap the spend, require a typed confirmation for real
execution, and append every decision to the ledger. See
`src/kinetic/ingestion/warehouse/bigquery/client.py`.

## Checklist

- [ ] Raw models mirror the provider's actual payload, including its oddities
- [ ] The normalizer is a pure function with no I/O
- [ ] No provider field leaks into a `kinetic.data` schema unmapped
- [ ] Unit tests need no network and no credentials
- [ ] The task is registered in `bootstrap.py` under a namespaced id
- [ ] `make validate` passes, including `make lint-imports`
