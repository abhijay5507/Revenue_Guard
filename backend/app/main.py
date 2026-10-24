from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.routes.webhooks import router as webhook_router
from backend.app.db.session import test_database_connection

app= FastAPI(title="RevenueGuard")
app.include_router(webhook_router, prefix="/api/v1")

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "revenueguard-api"
    }

@app.get("/")
def root():
    return "RevenueGuard API is running"

@app.get("/health/db")
def database_health_check():
    try:
        database_ok = test_database_connection()
    except SQLAlchemyError:
        database_ok = False

    return{
        "status": "ok" if database_ok else "error",
        "database": "connected" if database_ok else "disconnected",
    }
