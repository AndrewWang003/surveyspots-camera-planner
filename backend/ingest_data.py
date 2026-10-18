import asyncio
import os
import logging
from pathlib import Path

from app.database import (
    close_db,
    init_db,
    insert_survey_points,
    store_raw_upload,
)
from app.ingestion_service import persist_normalized_ingestion_layers
from app.kml_parser import extract_kml_text, parse_kml_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    await init_db()
    data_dir = "../data"
    files = os.listdir(data_dir)
    total_inserted = 0
    for filename in files:
        filepath = os.path.join(data_dir, filename)
        content = None
        if filename.lower().endswith((".kml", ".kmz")):
            content = Path(filepath).read_bytes()
        else:
            continue

        if content:
            raw_upload_id = await store_raw_upload(filename, content, uploaded_by="bulk-ingest")
            kml_text = extract_kml_text(content, filename)
            await persist_normalized_ingestion_layers(
                raw_upload_id,
                kml_text,
                filename,
            )
            points = parse_kml_file(kml_text, filename)
            ic_points = [point for point in points if point.layout_family == "ic"]
            logger.info(
                "File %s: extracted %d points, keeping %d IC points.",
                filename,
                len(points),
                len(ic_points),
            )
            if ic_points:
                inserted = await insert_survey_points(ic_points, filename, raw_upload_id)
                total_inserted += inserted
                logger.info(f"Inserted {inserted} points from {filename}")

    logger.info(f"Finished ingestion. Total points inserted: {total_inserted}")
    await close_db()

if __name__ == "__main__":
    asyncio.run(main())
