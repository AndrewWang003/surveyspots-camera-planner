import asyncio
from app.database import fetch_clustered_points, init_db, close_db
from app.models import ClusteredPoint
from app.kml_generator import generate_reference_kml

async def main():
    await init_db()
    try:
        records = await fetch_clustered_points(-37.816, 144.963)
        points = [ClusteredPoint(**dict(r)) for r in records]
        kml = generate_reference_kml(-37.816, 144.963, points)
        print(kml[:200])
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(main())
