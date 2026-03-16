from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.config import load_env
from app.config.db import check_db, create_tables, is_db_configured

from app.api import api_router
from app.middleware.auth import apply_auth_middleware

load_env()
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
        create_tables()


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
