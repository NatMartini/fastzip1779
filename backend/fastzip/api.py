from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from fastzip.config import settings
from fastzip.db import database, record_error
from fastzip.routes import admin, auth, jobs
from fastzip.storage import storage

app = FastAPI(
    title="FastZip API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    record_error("api", "Request failed due to an internal service error")
    return JSONResponse(
        status_code=503, content={"detail": "Service temporarily unavailable; please try again"}
    )


@app.get("/api/health/live")
def live():
    return {"status": "ok"}


@app.get("/api/health/ready")
def ready(db: DBSession = Depends(database)):
    db.execute(text("SELECT 1 FROM alembic_version"))
    storage().client.head_bucket(Bucket=settings().s3_bucket)
    return {"status": "ready"}


app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(admin.router)
