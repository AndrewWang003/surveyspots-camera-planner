"""CLI wrapper for exporting KML camera-layout training rows."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.training_exporter import export_training_dataset


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export relative camera-layout training rows from KML/KMZ files."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--rows-output",
        type=Path,
        default=Path("ai_handoff/training_rows.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("ai_handoff/training_summary.json"),
    )
    parser.add_argument("--line-match-radius-m", type=float, default=3.0)
    args = parser.parse_args()

    rows, summaries = export_training_dataset(
        args.data_dir,
        args.rows_output,
        args.summary_output,
        line_match_radius_m=args.line_match_radius_m,
    )
    skipped = [summary for summary in summaries if summary.status != "exported"]
    print(f"Exported {len(rows)} training rows to {args.rows_output}")
    print(f"Wrote {len(summaries)} file summaries to {args.summary_output}")
    if skipped:
        print("Skipped files:")
        for summary in skipped:
            print(f"  - {summary.source_file}: {summary.notes}")


if __name__ == "__main__":
    main()
