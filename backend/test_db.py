import asyncio
from app.database import fetch_clustered_points, init_db, close_db

async def main():
    await init_db()
    try:
        points = await fetch_clustered_points(-37.816, 144.963)
        print("Points:", points)
    except Exception as e:
        print("Error:", e)
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(main())
