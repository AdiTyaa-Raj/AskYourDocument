import logging
import os

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.config import load_env
from app.config.db import bootstrap_schema, check_db, is_db_configured

from app.api import api_router
from app.middleware.auth import apply_auth_middleware

load_env()

# ---------------------------------------------------------------------------
# Logging – configure once at process start so every logger in the app
# (upload endpoint, job worker, services, …) writes to stdout.
# Set LOG_LEVEL env var to DEBUG / WARNING etc. to override.
# ---------------------------------------------------------------------------
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Keep uvicorn's own loggers in sync so their output uses the same format.
for _uvicorn_logger in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(_uvicorn_logger).handlers = []
    logging.getLogger(_uvicorn_logger).propagate = True
app = FastAPI(
    title="AskYourDocument",
    swagger_ui_parameters={"persistAuthorization": True},
)
apply_auth_middleware(app)
app.include_router(api_router)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version="1.0.0",
        routes=app.routes,
        description=app.description,
    )
    components = openapi_schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    # Apply bearer auth globally in the docs. Public endpoints remain public server-side.
    openapi_schema["security"] = [{"BearerAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.on_event("startup")
def startup_db_bootstrap() -> None:
    if is_db_configured():
        bootstrap_schema()
        from app.services.document_job_worker import start_worker
        start_worker()


@app.get("/health", tags=["health"])
def health_check() -> dict:
    db_status = "not_configured"
    status = "ok"
    if is_db_configured():
        try:
            check_db()
            db_status = "ok"
        except Exception:
            db_status = "error"
            status = "degraded"
    return {"status": status, "db": db_status}
