"""Canonical, provider-independent data: schemas, catalog metadata, storage.

Nothing here knows which provider supplied a record. Provider request and
response shapes live in :mod:`kinetic.ingestion`; the mapping between the two is
the provider adapter's job.
"""
