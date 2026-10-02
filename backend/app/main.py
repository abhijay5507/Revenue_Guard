from fastapi import FastAPI

from backend.app.db.session import test_database_connection

app= FastAPI(title="RevenueGuard")

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
    database_ok= test_database_connection()

    return{
        "status": "ok" if database_ok else "error",
        "database": "connected" if database_ok else "disconnected",
    }