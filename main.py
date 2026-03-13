from fastapi import FastAPI

from app.api import api_router

app = FastAPI(title="AskYourDocument")
app.include_router(api_router)


@app.get("/health", tags=["health"])
def health_check() -> dict:
    return {"status": "ok"}
