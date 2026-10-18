"""Inspect and reset parsed survey history safely by parser version."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import asyncpg


def _load_backend_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_backend_env()

from app.config import settings  # noqa: E402
from app.database import close_db, fetch_latest_raw_uploads, init_db, insert_survey_points
from app.ingestion_service import persist_normalized_ingestion_layers
from app.kml_parser import extract_kml_text, parse_kml_file


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(settings.DATABASE_URL)


async def show_stats() -> None:
    conn = await _connect()
    try:
        raw_count = await conn.fetchval("SELECT COUNT(*) FROM raw_uploads;")
        raw_sources = await conn.fetchval("SELECT COUNT(DISTINCT source_file) FROM raw_uploads;")
        print(f"raw_uploads\tfiles={raw_sources}\tversions={raw_count}")
        rows = await conn.fetch(
            """
            SELECT
                parser_version,
                layout_family,
                equipment_type,
                COUNT(*) AS row_count
            FROM survey_history
            GROUP BY parser_version, layout_family, equipment_type
            ORDER BY parser_version, layout_family, equipment_type;
            """
        )
        for row in rows:
            print(
                f"{row['parser_version']}\t{row['layout_family']}\t"
                f"{row['equipment_type']}\t{row['row_count']}"
            )
    finally:
        await conn.close()


async def clear_current_parser() -> None:
    conn = await _connect()
    try:
        deleted = await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM survey_history
                WHERE parser_version = $1
                RETURNING 1
            )
            SELECT COUNT(*) FROM deleted;
            """,
            settings.HISTORY_PARSER_VERSION,
        )
        print(
            f"Deleted {deleted} rows for parser version "
            f"{settings.HISTORY_PARSER_VERSION}."
        )
    finally:
        await conn.close()


async def clear_all_history() -> None:
    conn = await _connect()
    try:
        async with conn.transaction():
            deleted = await conn.fetchval(
                """
                WITH deleted AS (
                    DELETE FROM survey_history
                    RETURNING 1
                )
                SELECT COUNT(*) FROM deleted;
                """
            )
            await conn.execute("DELETE FROM import_batches;")
        print(f"Deleted {deleted} survey_history rows and cleared import_batches.")
    finally:
        await conn.close()


async def clear_file(source_file: str) -> None:
    conn = await _connect()
    try:
        deleted = await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM survey_history
                WHERE source_file = $1
                  AND parser_version = $2
                RETURNING 1
            )
            SELECT COUNT(*) FROM deleted;
            """,
            source_file,
            settings.HISTORY_PARSER_VERSION,
        )
        print(
            f"Deleted {deleted} rows for {source_file!r} at parser version "
            f"{settings.HISTORY_PARSER_VERSION}."
        )
    finally:
        await conn.close()


async def rebuild_current_parser() -> None:
    """Rebuild active parsed rows from the latest stored raw upload per file."""
    await init_db()
    try:
        raw_uploads = await fetch_latest_raw_uploads()
        total_inserted = 0
        for raw_upload in raw_uploads:
            source_file = raw_upload["source_file"]
            content = bytes(raw_upload["content"])
            try:
                kml_text = extract_kml_text(content, source_file)
                await persist_normalized_ingestion_layers(
                    raw_upload["id"],
                    kml_text,
                    source_file,
                )
                parsed_points = parse_kml_file(kml_text, source_file)
            except Exception as exc:  # noqa: BLE001
                print(f"SKIP {source_file}: {exc}")
                continue

            ic_points = [
                point
                for point in parsed_points
                if point.layout_family == "ic"
            ]
            inserted = 0
            if ic_points:
                inserted = await insert_survey_points(
                    ic_points,
                    source_file,
                    raw_upload["id"],
                )
            total_inserted += inserted
            print(
                f"REBUILT {source_file}: parsed={len(parsed_points)} "
                f"ic={len(ic_points)} inserted={inserted}"
            )
        print(
            f"Finished rebuild for parser {settings.HISTORY_PARSER_VERSION}: "
            f"{total_inserted} rows inserted."
        )
    finally:
        await close_db()


async def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("stats")
    subparsers.add_parser("clear-current-parser")
    subparsers.add_parser("clear-all-history")
    subparsers.add_parser("rebuild-current-parser")
    clear_file_parser = subparsers.add_parser("clear-file")
    clear_file_parser.add_argument("source_file")
    args = parser.parse_args()

    if args.command == "stats":
        await show_stats()
    elif args.command == "clear-current-parser":
        await clear_current_parser()
    elif args.command == "clear-all-history":
        await clear_all_history()
    elif args.command == "rebuild-current-parser":
        await rebuild_current_parser()
    elif args.command == "clear-file":
        await clear_file(args.source_file)


if __name__ == "__main__":
    asyncio.run(main())
