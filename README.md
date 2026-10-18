# SurveySpots

A Chrome extension and spatial backend that suggest where to install traffic-survey cameras, by reusing camera positions that have already worked at the same sites in past surveys.

> This is a cleaned public copy. Real survey files, project numbers, place names and coordinates have been removed; tests that need the private survey corpus are skipped.

## The problem

I install traffic-survey cameras in the field. Before each survey, every camera position is planned and marked on a Google My Maps map for the field team.

Many sites have been surveyed before, and the old maps already show camera positions that worked in practice. But that knowledge is hard to reuse:

- it is spread across many separate project maps;
- layers are named inconsistently (`IC`, `Cams`, `cam locations`, `Task 1- IC` ...);
- camera points are mixed with other things on the same map: driver routes, tube counters, coverage areas, queue-length tasks;
- different survey types need different cameras and must not be mixed up.

So planning each new job means searching old maps by hand, or starting again.

## How I approached it

The main idea: **treat old survey maps as evidence, and only suggest positions that have really been used.**

1. **Reuse real positions, never invent them.** My first version tried to learn camera offsets and predict new positions. I dropped it. A predicted point can land somewhere you cannot install a camera. Now every suggested camera is an exact historical coordinate; when evidence is missing, the site is flagged for review instead of guessed.
2. **Keep raw evidence unchanged.** Uploaded KML/KMZ files are stored byte-for-byte. Everything derived from them (sites, cameras, directions, clusters) is versioned, so when the parser improves, the data can be rebuilt without asking anyone to upload again. Human decisions are never overwritten by a rebuild.
3. **A person reviews messy input before it becomes history.** Each upload is previewed first. The user confirms which layers count as historical evidence and what type they are. Nothing is written to the database until that review is confirmed.
4. **Separate the distance rules.** One radius was not enough. Field testing showed 25 m was too tight and 250 m too broad. The system now uses separate rules: 75 m to match a new site to a historical site, 25 m to recognise the same site across different surveys, and 1.5 m to treat two camera points as the same physical position.
5. **Pick one coherent layout, not a union.** If a site was surveyed three times with slightly different setups, merging them would produce too many cameras. Each project gets one vote (latest revision wins), the most common camera count wins, and the output is one complete historical layout.
6. **Respect survey types.** Intersection-count cameras are only used for intersection sites; origin-destination layouts can share positions with each other; tube-counter (ATC) data is kept but never used for cameras.
7. **Keep it simple where the platform is fixed.** Google My Maps has no clean official export API, so the workflow uses manual KML/KMZ export rather than scraping private endpoints.

Every decision keeps its reasons and rule version, so a future AI-assisted review step can be added without losing the audit trail.

## What it does

1. The planner drops site pins for a new job in Google My Maps and exports the map.
2. The extension uploads it. The backend matches each pin to historical sites and selects camera positions.
3. The planner downloads a KMZ with camera points and facing lines and imports it back into My Maps, with a quality summary of which sites were complete, partial or need review.

Tested against a private corpus of real historical surveys (about 170 sites and 180 camera positions).

## Built with

Chrome Extension (Manifest V3), FastAPI, PostgreSQL + PostGIS (spatial queries and DBSCAN clustering), Python. Built with AI coding agents (Codex and Claude): I defined the problem, rules and acceptance checks from my field experience, and reviewed the results. `Developmentlog.md` records how the design changed and why.

## Tests

```bash
cd backend
python -m unittest discover
```

In this public copy, 66 tests run and pass; 15 tests need the private survey corpus and are skipped.

## Status

Working MVP, used on real survey data. Not deployed for a team: authentication is still in local development mode.

## Licence

Copyright (c) 2026 Andrew Wang. All rights reserved. The source is shared so it can be read and reviewed; please ask before reusing it.

---

## Technical details

### Current MVP

- Historical KML/KMZ import uses a preview and Layer review before database writes.
- Raw Placemark geometry and source metadata remain immutable evidence.
- Sites, Camera Locations, Direction Lines, Camera-to-Site relationships, Camera clusters, and Canonical Sites are normalized into versioned derived data.
- A three-point Camera Direction without a nearby Camera icon derives its Camera Location from Point 2 while preserving the original LineString as source evidence.
- ATC logger Points and pneumatic tube LineStrings are stored with an independent ATC task family and never participate in Camera generation.
- A user Site Point matches the nearest compatible Canonical Site within 75 metres.
- Generation request Site Points can infer a task family from the My Maps folder/layer, Placemark name, or description, such as `IC`, `OD`, `IPOD`, `HIK`, or `ATC`.
- IC Cameras are used only for IC Sites; HIK Cameras are used only for HIK Sites; ATC remains independent; OD and IPOD are compatible and may share Camera Locations.
- If a matched Site has insufficient Camera history, generation can borrow a task-compatible nearby Canonical Site layout within 50 metres as supplemental evidence.
- Explicit `X cameras` descriptions take priority; otherwise the historical count mode is used; equal-frequency count modes resolve to the larger Camera count.
- If no explicit, historical, or nearby Canonical count evidence exists, generation uses a default target of 2 Cameras but only emits real historical Camera coordinates.
- The generator preserves strict primary, fallback, and nearby evidence tiers, then prefers the most frequent historical layout within a tier.
- Processed KMZ output currently includes generated Camera Locations and Direction Lines only. Site output is deferred until the future Polygon workflow.
- Generated Camera Locations use short field-facing names such as `IC 7 Cam 1`, default to the black My Maps Camera icon, and keep descriptions intentionally brief.

The deterministic rule engine is the default path. Raw evidence and decision provenance are retained so reviewable AI classification or output checking can be added later.

### Local Setup

Requirements: Docker Desktop, Python 3.11 or newer, and Chrome.

```bash
cp backend/.env.example backend/.env
./start.sh
```

`start.sh` starts the existing PostGIS container, creates `backend/.venv` when needed, and runs the API at [http://127.0.0.1:8000](http://127.0.0.1:8000). It does not apply schema changes or load sample data.

Load the Extension:

1. Open `chrome://extensions/`.
2. Enable Developer mode.
3. Select **Load unpacked**.
4. Choose the `extension` folder.

The Extension currently uses the local development authentication flow. Keep the existing Bearer-token request path; production Google token validation is intentionally deferred until after MVP testing.

### Database Control

Daily database operations are deliberately small and non-destructive:

```bash
./scripts/db.sh start
./scripts/db.sh status
./scripts/db.sh stop
```

The script reuses the existing `postgis` container and its current data volume. If the container does not exist, `start` creates it with a persistent `surveyspots_postgis_data` Docker volume.

Schema migration, health audits, backup, restore, and rebuild are maintenance operations. Confirm the intended action and database with the project maintainer before running them. These actions are not bundled into daily startup.

### User Workflows

#### Generate Camera Locations

1. Draw or add Site Points in Google My Maps.
   Use clear folder or layer names such as `IC Sites`, `OD Sites`, `IPOD Sites`, `HIK Sites`, or `ATC Sites` when the request contains more than one task type or sits near historical Sites from multiple task families.
2. Export the map as KML/KMZ.
3. Select **Upload Pinned Site Map** in the Extension.
4. Review the Site and Camera quality summary.
5. Download the processed KMZ.

The processed KMZ contains only generated Camera Location placemarks and Direction Lines. It does not emit Site placemarks in the current MVP.

Generated Camera names are based on the request Site name and selected Camera rank. For example, a request Site named `IC 7` produces names such as `IC 7 Cam 1`; a request Site named `Site 8, Main Road` produces names such as `Site 8, Main Road Cam 1`.

When all request Site markers use one colour, generated Camera markers use the default black My Maps Camera icon. When the request map uses multiple Site marker colours, generated Camera markers inherit the corresponding request Site colour.

The Extension does not require the user to choose a generation target manually. It infers the target task from the uploaded map structure and only matches compatible historical evidence. OD and IPOD are treated as compatible Camera evidence; IC, HIK, and ATC stay separate.

If the system has no count evidence for a matched Site, it uses a default target of 2 Cameras. This target does not create artificial locations: if only one reliable historical Camera Location is available nearby, only one Camera placemark is emitted.

#### Import Historical Data

1. Select **Upload History KML**.
2. Review detected Layers, geometry, and business-role counts.
3. Include only Layers intended as historical evidence.
4. Set one task family for each included Layer, such as IC, OD, IP OD, HIK, ATC, Site, or Other.
5. Set every allowed data tag that may appear in that Layer, such as Site, Camera, Direction, Polygon, Route, or ATC.
6. Confirm the import.

Preview and generation uploads do not write historical data. Only a confirmed historical import writes to the database. Duplicate raw files are rejected by a database-level unique content hash.

Mixed Layers are expected. For example, a small IC Layer may contain Site Points, Camera Points, Direction Lines, and Coverage Polygons. In that case, keep the task family as IC and select all matching data tags; each Placemark is still classified by geometry, name, description, and the reviewed tags.

Choose **ATC** for an ATC Layer during review. ATC data is retained for a future ATC-specific generator; the current Camera generator ignores it.

### API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | API and database health |
| `POST` | `/api/v1/pinned-map-reference-kml` | Read-only Site Point generation to Camera/Direction KMZ |
| `POST` | `/api/v1/preview-upload` | Parse historical files without database writes |
| `POST` | `/api/v1/confirm-upload` | Persist reviewed historical Layers |

### Verification

Run the side-effect-free regression suite from the backend directory:

```bash
cd backend
DATABASE_URL=postgresql://postgres:surveypass@localhost:5432/postgres \
  .venv/bin/python -m unittest discover
```

Customer KML/KMZ files are source evidence and are not committed. Repository code, comments, UI text, tests, and maintenance documentation must remain in English.
