<p align="center">
  <img src="./logo.svg" width="128" alt="LoadOut logo" />
</p>

<h1 align="center">LoadOut</h1>

<p align="center">
  Local-first wardrobe inventory, trip planning, and packing execution.
</p>

<p align="center"><strong>Version 0.3.0</strong></p>

LoadOut tracks individual possessions across homes and travel containers, turns trip requirements into explainable packing candidates, and keeps recommendations separate from what physically happened. It includes a React interface, a FastAPI service, and a Python CLI backed by human-readable YAML.

## Screenshots

The UI below is rendered exclusively from the sanitized catalog in `data/examples/`.

![LoadOut wardrobe overview](./assets/screenshots/wardrobe-overview.jpg)

![LoadOut item drawer](./assets/screenshots/item-drawer.jpg)

![LoadOut trip packing interface](./assets/screenshots/trip-packing.jpg)

## Why LoadOut

- **Know where everything is.** Every physical item has a current location, preferred home, condition, status, and movement history.
- **Plan without changing reality.** Trips and packing plans are recommendations until a separately confirmed execution action is applied.
- **Pack with context.** Plans can account for multi-leg itineraries, weather, laundry, activities, items already at a destination, and available luggage.
- **Keep an audit trail.** Packed, used, unused, rejected, transferred, purchased, damaged, discarded, lost, returned, and destination-stay outcomes are recorded explicitly.
- **Own your data.** The app runs locally and stores records as YAML. Personal runtime files are ignored by Git by default.

## Architecture

```text
React + Vite (web/)  ->  FastAPI (api/)  ->  inventory_toolkit/  ->  YAML (data/)
        UI                    adapter          domain rules           local state
```

The browser never edits YAML directly. All reads, movement previews, confirmations, packing decisions, and execution actions pass through the validated Python domain layer.

## Quick start

Requirements:

- Python 3.9+
- Node.js 18+

Create a private local dataset from the sanitized starter files:

```sh
cp data/examples/*.yaml data/
```

Install and start the API from the repository root:

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/uvicorn api.app:app --reload
```

In a second terminal, start the web app:

```sh
cd web
npm install
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173).

## Data model

LoadOut deliberately separates four kinds of information:

| Layer | Purpose | Can change physical inventory? |
| --- | --- | --- |
| Trip facts | Dates, legs, destinations, luggage, activities, planning context | No |
| Packing plans | Nine recommendation sections with reasons | No |
| Execution ledger | Confirmed real-world outcomes and reconciliation | Only through an explicit confirmed action |
| Inventory | Physical instances, locations, state, and movement history | Yes, with confirmation and source validation |

Runtime files live in `data/`:

| File | Contents | Git policy |
| --- | --- | --- |
| `schema.yaml` | Validation schema | Tracked |
| `activity_templates.yaml` | Reusable activity requirements | Tracked |
| `requirement_matchers.yaml` | Deterministic category-to-item matching rules | Tracked |
| `clothes.yaml` | Item definitions and physical instances | **Ignored** |
| `locations.yaml` | Homes and travel containers | **Ignored** |
| `trips.yaml` | Itineraries and planning facts | **Ignored** |
| `packing_plans.yaml` | Recommendation-only plans | **Ignored** |
| `trip_executions.yaml` | Confirmed actions and reconciliation history | **Ignored** |

The sanitized files in `data/examples/` form a safe, complete starter catalog:
representative possessions, a three-leg trip, all nine packing-plan sections,
and a partial execution ledger. Copy them into `data/`, then replace the
example records with your own local information.

## Safety model

LoadOut treats a suggestion, a confirmation, and a physical outcome as different events:

1. A movement or packing action is previewed.
2. The user explicitly confirms the exact action.
3. The domain layer verifies item IDs, expected source locations, and destinations.
4. The write is validated and applied atomically.
5. Movement or execution history records the factual outcome.

Packing-plan confirmation alone never moves an item. Preferred locations remain unchanged unless a separate preferred-home update is requested. A trip cannot be completed until returns, destination stays, unused items, purchases, incidents, and rejected or changed recommendations have all been reviewed.

## CLI

Installing the Python package registers the `inventory` command. The repository-local `./inventory` wrapper provides the same interface.

```sh
# Validate the complete local dataset
inventory validate

# Browse possessions and containers
inventory locations
inventory list --location home
inventory search "rain jacket"
inventory contents carry-on
inventory away

# Preview a physical move; no state is written
inventory move home-rain-jacket --from home --to carry-on \
  --reason "Packed for trip"

# Apply the same exact move only after review
inventory move home-rain-jacket --from home --to carry-on \
  --reason "Packed for trip" --confirm

# Inspect trip readiness and packing candidates
inventory trip readiness sample-trip
inventory --json packing context sample-trip
inventory --json packing matches sample-trip --requirement outer_layer
```

Run `inventory --help` or `inventory <command> --help` for the full command surface.

## Development

```sh
# Fast generalized suite: in-memory domain/API coverage plus five YAML workflows
./.venv/bin/pytest

# Only the durable YAML end-to-end contracts
./.venv/bin/pytest -m e2e

# Optional read-only compatibility check against your local catalog
./.venv/bin/pytest -o addopts=-q -m personal_data

# Historical tests coupled to the original private catalog (normally skipped)
./.venv/bin/pytest -o addopts=-q -m legacy_personal

# Frontend type-check and production build
cd web
npm run build
```

The default suite is intentionally independent of the local wardrobe. It uses
the complete synthetic lifecycle in `data/examples/`, an injected in-memory
repository for domain and API tests, property-based invariant checks, and five
marked YAML adapter workflows. Pytest prints the ten slowest cases on every
run. The only default-excluded checks are the read-only local compatibility
smoke test and superseded historical tests tied to a private catalog.

The detailed trip notes in `docs/` remain intentionally ignored. Before
publishing that directory, replace all real locations, dates, names, item IDs,
and itinerary details with synthetic fixtures.

## Privacy guidelines

LoadOut stores personal inventory and itinerary data as local YAML files, and `.gitignore` excludes those files by default. That protection only holds until a file is committed or force-added, so anyone sharing or publishing a copy of this repository should follow a few practices:

- Confirm nothing personal is staged before pushing:

  ```sh
  git status --short --ignored
  git diff --cached
  git check-ignore -v \
    data/clothes.yaml data/locations.yaml data/trips.yaml \
    data/packing_plans.yaml data/trip_executions.yaml
  ```

- Never commit booking confirmations, boarding passes, calendar exports, receipts, passport details, or environment files.
- If sensitive data ever enters Git history, removing the working-tree file is not enough. Rotate any exposed credentials or booking references where possible and rewrite the repository history before publishing.

## Project layout

```text
api/                    FastAPI adapter
inventory_toolkit/      Validation, queries, planning, packing, and execution
scripts/                Migration and standalone validation utilities
web/                    React + Vite interface
data/                   Local YAML state plus shareable rules
data/examples/          Sanitized starter dataset
logo.svg                LoadOut mark and README artwork
```

## License

No license has been added yet. Until one is chosen, the source is not automatically licensed for redistribution.
