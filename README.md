# Definitio

**An agent-native GIS runtime** — a semantic contract over MCP, spatial-semantics
verification before execution, and an auditable receipt for every action.

Status: **pre-certification (NOT_CERTIFIED)** · Version 0.2.0-dev · Interface `definitio-agent-v1`

## Why

Most "AI + GIS" work bolts a language model onto a desktop GIS and lets it drive
the GUI or a vendor API. Definitio takes the opposite route: the runtime is
designed for agents first. GIS operations are declared as verbs with explicit
schemas; multi-step work is planned and dry-run before anything is written;
results are verified against spatial-semantic predicates — not just internal
consistency — and every committed action returns a receipt bound to the
persisted artifact it produced.

## The three pillars

1. **Semantic contract over MCP.** 40 GIS verbs are declared in
   [`definitio/catalog.json`](definitio/catalog.json) and served over MCP
   (a compact router profile by default; `--tool-profile full` exposes all 60
   tools — the 40 verbs plus management tools). The same implementation is
   available over HTTP (`GET /api/agent/manifest`, `POST /api/agent/call`).
   See [AGENT.md](AGENT.md) for the agent-facing operating guide.
2. **Verification before execution.** A plan can be dry-run in memory with the
   same code that will execute it, reporting per-step effects before any write.
   At execution time, input fingerprints are re-checked and results must pass
   spatial-semantic verification (containment, provenance, multiplicity,
   exact-encoding checks) before they are committed.
3. **A receipt for every action.** Each committed task returns an immutable
   receipt. Replays and re-verification re-read the live artifact — bytes,
   size, SHA-256, content binding — instead of trusting history, and report
   `TARGET_STATE_UNKNOWN` when current state cannot be confirmed.

## Repository layout

| Path | What it is |
| --- | --- |
| `definitio/` | The engine package: runtime, MCP server, HTTP server, planner, verifier, authorization, store |
| `run_engine.py`, `run_mcp.py` | Entry points for the engine and the MCP adapter |
| `scripts/` | Self-checks, environment doctor, audits, and workers for optional native backends |
| `tests/` | The pytest suite |
| `examples/` | Sample data (GeoJSON, LAS) |
| `docs/` | Handbook, contract addendum, clean-install guide, and the [red-team record](docs/red-team-review-2026-11.md) |
| `AGENT.md` | Operating guide written for agents connecting to the runtime |

## Quick start

Requirements: **Python 3.12** (developed and verified on 3.12.9). The core
needs no system GDAL/QGIS/PDAL installation — the wheels carry GEOS/PROJ/GDAL.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt     # POSIX

# Self-checks (in-process; binds no port)
python -X utf8 scripts/clean_install_selftest.py --out evidence/clean-install-selftest.json
python -X utf8 scripts/environment_doctor.py --profile core

# Run the engine, then the MCP adapter (which can also autostart an engine)
python run_engine.py --port 8765 --data-dir ./data
python run_mcp.py --engine-url http://127.0.0.1:8765 --data-dir ./data
```

Then point your MCP client at `run_mcp.py`. For the full from-scratch
procedure, see [docs/CLEAN-INSTALL.md](docs/CLEAN-INSTALL.md).

Tests (use `python -m pytest`, not bare `pytest`):

```bash
python -m pytest tests/ -q
```

Each verb declares its dependency tier. The core verbs run on the
pip-installable stack alone; extended verbs light up when native backends
(QGIS, ArcPy, PDAL, PostGIS, GeoServer, SensorThings) are present. A subset of
tests targets those optional backends and reports them unavailable — without
issuing network calls — on a core-only machine.

## Status

- **Certification: NOT_CERTIFIED.** No conformance or certification gate has
  been issued for this runtime. "Pre-certification" above means exactly that.
- **Red-team record.** An independent red-team review on 2026-11-16 returned
  NO_GO with 11 issue groups and 13 reproducible counterexamples. All 13 were
  fixed, and an independent acceptance on 2026-11-17 re-verified the fix
  (0/13 reproduce) with limitations recorded in
  [docs/red-team-review-2026-11.md](docs/red-team-review-2026-11.md).
  That acceptance covers the fix — it is not a certification.
- **Known limits.** A clean-machine install needs a package index that serves
  the pinned versions in `requirements.txt`; heavy GIS backends are not
  bundled.

## License

Dual-licensed:

- **Code:** Apache License 2.0 — see [LICENSE](LICENSE).
- **Documentation and specification:** CC BY 4.0 — see
  [LICENSE-CC-BY-4.0](LICENSE-CC-BY-4.0).

Copyright 2026 Huangyu Rao.

## Contact

Maintained by [@YUYU20061107](https://github.com/YUYU20061107). Please use
this repository's issue tracker for questions and reports.
