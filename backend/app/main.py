import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db, close_db
from .routes import kml_routes
from .models import HealthResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI application lifecycle."""
    logger.info("Starting up FastAPI application...")
    await init_db()
    yield
    logger.info("Shutting down FastAPI application...")
    await close_db()

app = FastAPI(
    title="Smart Traffic Survey Deployment Assistant API",
    version="1.0.0",
    description="Spatial API for Google My Maps Chrome Extension integration",
    lifespan=lifespan,
)

# CORS configuration — allow all origins during local development
# (chrome-extension:// origins require exact ID matching, so wildcard is simplest for dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(kml_routes.router)

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check():
    """Health check endpoint to verify system status."""
    return HealthResponse(
        status="ok",
        version=app.version,
        database="connected"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
