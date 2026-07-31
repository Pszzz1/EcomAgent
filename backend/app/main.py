from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api.release_task import router as release_task_router
from backend.app.infrastructure.settings import load_settings
from backend.app.services import ReleaseTaskAgentService


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(
    service: ReleaseTaskAgentService | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        managed_service = service or ReleaseTaskAgentService()
        app.state.release_task_service = managed_service
        try:
            yield
        finally:
            if service is None:
                managed_service.close()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.api_cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(release_task_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    static_directory = frontend_dist or FRONTEND_DIST
    if static_directory.is_dir():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")

    return app


app = create_app()
