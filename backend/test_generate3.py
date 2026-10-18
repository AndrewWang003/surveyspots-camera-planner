import asyncio
from app.database import fetch_clustered_points, init_db, close_db
from app.models import ClusteredPoint
from app.kml_generator import generate_reference_kml

async def main():
    await init_db()
    try:
        # Huge radius to fetch all 182 points
        records = await fetch_clustered_points(-37.816, 144.963, radius_m=9000000.0)
        points = [
            ClusteredPoint(
                lat=r['representative_lat'],
                lng=r['representative_lng'],
                azimuth=r['latest_azimuth'],
                equipment_type=r['equipment_type'],
                total_usage=r['total_usage'],
                record_count=r['record_count'],
                distance_m=r['distance_m'],
                latest_timestamp=r['latest_timestamp']
            ) for r in records
        ]
        print(f"Fetched {len(points)} points")
        kml = generate_reference_kml(-37.816, 144.963, points, fade_r=9000000.0, core_r=4500000.0)
        print("Success! Generated KML length:", len(kml))
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(main())
