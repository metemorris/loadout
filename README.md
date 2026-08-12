<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./logo-light.svg">
    <img src="./logo-light.svg" width="112" alt="LoadOut logo">
  </picture>
</p>

<h1 align="center">LoadOut</h1>

<p align="center">
  <strong>Your wardrobe, mapped from closet to carry-on.</strong><br>
  Local-first inventory, explainable trip planning, and confirmed packing execution.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#cli">CLI</a> ·
  <a href="#development">Development</a>
</p>

<p align="center">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/Python-3.9%2B-111111?style=flat-square&logo=python&logoColor=white">
  <img alt="React 19" src="https://img.shields.io/badge/React-19-111111?style=flat-square&logo=react&logoColor=white">
  <img alt="MIT license" src="https://img.shields.io/badge/License-MIT-111111?style=flat-square">
  <img alt="Version 0.3.0" src="https://img.shields.io/badge/version-0.3.0-111111?style=flat-square">
</p>

LoadOut is a self-hosted toolkit for tracking individual possessions, building packing plans from real trip requirements, and recording what actually happened. The React app, FastAPI service, and Python CLI all operate on readable YAML stored on your machine.

## At a glance

| Inventory | Planning | Execution |
| --- | --- | --- |
| Track each physical item, its condition, current location, and preferred home. | Turn itineraries, weather, activities, and laundry access into reasoned candidates. | Confirm every state change and retain packed, used, returned, lost, or damaged outcomes. |

## Product tour

<table>
  <tr>
    <td width="50%"><img src="./assets/screenshots/wardrobe-overview.jpg" alt="Wardrobe overview"></td>
    <td width="50%"><img src="./assets/screenshots/item-drawer.jpg" alt="Item detail drawer"></td>
  </tr>
  <tr>
    <td align="center"><strong>See the whole wardrobe</strong></td>
    <td align="center"><strong>Inspect every physical item</strong></td>
  </tr>
  <tr>
    <td colspan="2"><img src="./assets/screenshots/trip-packing.jpg" alt="Trip packing interface"></td>
  </tr>
  <tr>
    <td colspan="2" align="center"><strong>Plan a trip without pretending the bag is already packed</strong></td>
  </tr>
</table>

> The interface shown here uses only the sanitized catalog in [`data/examples/`](./data/examples/).

## Why LoadOut

- **Know where everything is.** Every physical item has a current location, preferred home, condition, status, and movement history.
- **Plan without changing reality.** Trips and packing plans are recommendations until a separately confirmed execution action is applied.
- **Pack with context.** Plans can account for multi-leg itineraries, weather, laundry, activities, items already at a destination, and available luggage.
- **Estimate bag capacity.** Travel containers define rough volume/load limits, while every inventory type supplies compressed-volume and weight defaults.
- **Keep an audit trail.** Packed, used, unused, rejected, transferred, purchased, damaged, discarded, lost, returned, and destination-stay outcomes are recorded explicitly.
- **Own your data.** The app runs locally and stores records as YAML. Personal runtime files are ignored by Git by default.

## How it works

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
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/uvicorn api.app:app --reload
```

In a second terminal, start the web app:

```sh
cd web
npm ci
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

Capacity figures are intentionally estimates. A travel-container location has
`capacity_liters` and `max_load_kg`; `definitions.type_defaults` assigns
`default_space_liters` and `default_weight_kg` to every allowed item type. The
UI totals only inventoried contents, so the bag itself and untracked toiletries,
electronics, documents, and purchases must be allowed for separately.

## Safety model

LoadOut treats a suggestion, a confirmation, and a physical outcome as different events:

1. A movement or packing action is previewed.
2. The user explicitly confirms the exact action.
3. The domain layer verifies item IDs, expected source locations, and destinations.
4. The write is validated and applied atomically.
5. Movement or execution history records the factual outcome.

Packing-plan confirmation alone never moves an item. Preferred locations remain unchanged unless a separate preferred-home update is requested. A trip cannot be completed until returns, destination stays, unused items, purchases, incidents, and rejected or changed recommendations have all been reviewed.

## CLI

Installing the Python package registers the `inventory` command.

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

# Reconcile actions left pending by an interrupted inventory/ledger write.
# This rechecks each exact source and destination before applying anything.
inventory execution recover sample-trip-execution --confirm
```

Run `inventory --help` or `inventory <command> --help` for the full command surface.

## Development

```sh
# Fast generalized suite: in-memory domain/API coverage plus durable YAML workflows
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

GitHub Actions runs the generalized suite on Python 3.9 and 3.12, builds and
installs the wheel outside the checkout, verifies the Ruby compatibility entry
point, and builds the frontend from `package-lock.json`.

`scripts/validate_inventory.rb [DATA_DIR]` is a compatibility wrapper around
the Python validator so there is only one schema implementation. The legacy
inventory migration is non-writing by default: inspect it with
`scripts/migrate_inventory_v2.rb --dry-run --path PATH`, then pass `--confirm`
to write an atomic migration and retain a `.v1.bak` backup.

The default suite is intentionally independent of the local wardrobe. It uses
the complete synthetic lifecycle in `data/examples/`, an injected in-memory
repository for domain and API tests, property-based invariant checks, and
marked YAML adapter workflows, including interrupted-write recovery. Pytest
prints the ten slowest cases on every
run. The only default-excluded checks are the read-only local compatibility
smoke test and superseded historical tests tied to a private catalog.

The detailed trip notes in `docs/` remain intentionally ignored. Before
publishing that directory, replace all real locations, dates, names, item IDs,
and itinerary details with synthetic fixtures.

## Local service security

The API has no user accounts or authentication. It is designed for one user on
one machine, binds to `127.0.0.1`, and only permits the local Vite origins by
default. Do not expose port 8000 to a public network or place this service
behind a public reverse proxy without adding authentication and authorization.

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

LoadOut is released under the [MIT License](./LICENSE). See
[ASSETS.md](./ASSETS.md) for the visual-asset provenance record, including the
embedded OpenAI C2PA Content Credentials.
