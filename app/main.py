from fastapi import FastAPI

from .analyzer import analyze
from .schemas import DeadlockRequest, DeadlockResponse

app = FastAPI(
    title="Offline Two-Party Message Protocol Deadlock Analyzer",
    version="1.0.0",
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/deadlocks", response_model=DeadlockResponse)
def detect_deadlocks(request: DeadlockRequest) -> dict:
    return analyze(request)
