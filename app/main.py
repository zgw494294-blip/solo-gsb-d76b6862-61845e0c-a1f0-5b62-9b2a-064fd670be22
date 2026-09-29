from fastapi import FastAPI

from .analyzer import analyze, plan_completion
from .schemas import (
    CompletionRequest,
    CompletionResponse,
    DeadlockRequest,
    DeadlockResponse,
)

app = FastAPI(
    title="Offline Two-Party Message Protocol Deadlock Analyzer",
    version="1.1.0",
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/deadlocks", response_model=DeadlockResponse)
def detect_deadlocks(request: DeadlockRequest) -> dict:
    return analyze(request)


@app.post("/v1/completions", response_model=CompletionResponse)
def plan_completions(request: CompletionRequest) -> dict:
    return plan_completion(request)
