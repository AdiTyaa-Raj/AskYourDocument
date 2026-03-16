from fastapi import FastAPI

from app.config import load_env
from app.config.db import check_db, create_tables, is_db_configured

from app.api import api_router
from app.middleware.auth import apply_auth_middleware

load_env()
app = FastAPI(title="AskYourDocument")
apply_auth_middleware(app)
app.include_router(api_router)


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
