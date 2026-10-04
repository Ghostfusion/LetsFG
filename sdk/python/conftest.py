"""Pytest configuration for the LetsFG Python SDK.

Quarantined test modules — ``collect_ignore_glob``
--------------------------------------------------

Two groups of tests here were written against local implementations that no
longer exist, and both make ``pytest -m "not live"`` fail the required
``python-deterministic`` CI job (``.github/workflows/test.yml`` runs exactly
that command).

**Group 1 — 16 modules that fail at import (``ModuleNotFoundError``).**
They import ``letsfg.connectors.*`` packages that were removed in commit
``f91be5b`` ("feat: remove local connectors, route all search through PFS cloud
API", June 2026). Search and checkout now run server-side at letsfg.co, and
``letsfg/connectors/`` ships only ``auth.py`` (OAuth) and ``airport_tz.py``
(timezone helpers). Because the error is raised at import time, collection
aborts with 16 errors before any test runs.

**Group 2 — 2 modules that collect but fail at runtime.**
They exercise the local connector-era resolver and telemetry helpers that were
deleted with the same change:
  * ``test_india_user_surfaces.py`` calls ``letsfg.local._resolve_location_local``
    synchronously; it is now an ``async`` stub that returns ``[]`` because
    location resolution is handled server-side.
  * ``test_telemetry_enrichment.py`` imports ``_build_telemetry_payload`` and
    ``_fire_telemetry`` from ``letsfg.local``; neither exists any more.

Quarantined rather than deleted so the intent stays visible and can be re-homed
if these behaviours return to this repository. Each entry is one explicit path,
so the quarantine cannot silently widen: a newly added uncollectable or failing
module still fails the gate loudly.

To resolve this properly, either delete these modules or rewrite them against
the current API. When that happens, remove the corresponding entries here and
eventually this whole block.
"""

# Paths are relative to this conftest.py (the pytest rootdir is sdk/python).
collect_ignore_glob = [
    # --- Group 1: import-time failures from removed `letsfg.connectors.*` ---

    # Original connector parsing suite (removed connectors)
    "tests/test_ancillary_connector_parsing.py",
    "tests/test_connector_parsing.py",
    # Per-airline connector tests
    "tests/test_emirates_connector.py",
    "tests/test_skyscanner_connector.py",
    "tests/test_tripcom_connector.py",
    "tests/test_vueling_connector.py",
    "tests/test_wizzair_connector.py",
    # Checkout engine / booking-holdings
    "tests/test_booking_holdings_booking_urls.py",
    # Source/country filtering and regional source selection
    "tests/test_country_filter_completeness.py",
    "tests/test_india_direct_sources.py",
    "tests/test_india_ota_sources.py",
    "tests/test_regional_cross_area_validation.py",
    "tests/test_regional_india_sources.py",
    "tests/test_source_country_filter.py",
    # Connector-sourced data shaping
    "tests/test_currency_rates.py",
    "tests/test_starlink_flights.py",

    # --- Group 2: runtime failures from removed local resolver/telemetry ---
    "tests/test_india_user_surfaces.py",
    "tests/test_telemetry_enrichment.py",
]
