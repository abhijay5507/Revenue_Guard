from fastapi import FastAPI

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

