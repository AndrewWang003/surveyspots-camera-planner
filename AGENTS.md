# Project Instructions

## Development Structure

- Develop new application features as modular components whenever practical.
- Record every functional change in `Developmentlog.md` as part of the same change.

## Project Language

- Use English for all repository content, including code, identifiers, comments, UI copy, tests, logs, and maintenance documentation.
- Chinese may be used in developer conversations, but it must not be introduced into repository artifacts.
- Preserve customer-provided KML/KMZ content exactly as source evidence. Source names and source metadata are not translated or rewritten.

## Algorithm and AI Extensibility

- Keep the deterministic, explainable rule engine as the default processing path.
- Preserve raw observations and decision provenance so future AI-assisted historical-data classification and generated-result review can be added without rebuilding source evidence.
- Every automated decision must retain its method, version, confidence, reasons, and source references.
- AI output must remain reviewable and must not overwrite raw evidence or human-confirmed decisions.
- Human-confirmed decisions take precedence over rules and AI suggestions.
