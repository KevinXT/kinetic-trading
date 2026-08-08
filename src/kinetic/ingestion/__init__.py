"""Everything that talks to an external data provider.

Each provider is visibly isolated under its own subpackage — ``news/gdelt``,
``market/alpaca``, ``warehouse/bigquery`` — and is responsible for its own HTTP
client, authentication, retries, pagination, raw response models, and the
mapping from those raw models into canonical :mod:`kinetic.data` schemas.

Shared ingestion machinery (response caching, spend guardrails, date-window
resolution, the provider protocol and factory registry) lives at this level
because it exists only to serve provider calls.
"""
