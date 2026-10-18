import asyncio
from app.kml_parser import parse_kml_file
from app.database import insert_survey_points, init_db, close_db

async def main():
    await init_db()
    with open("../testdata/survey-b-od-cameras.kml", "r") as f:
        content = f.read()

    try:
        points = parse_kml_file(content, "survey-b-od-cameras.kml")
        print(f"Successfully parsed {len(points)} points")
        inserted = await insert_survey_points(points, "survey-b-od-cameras.kml")
        print(f"Successfully inserted {inserted} points")
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(main())
