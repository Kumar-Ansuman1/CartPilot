from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from backend.app.commerce_agent import (
    CommerceAgentResult,
    run_commerce_agent,
)


class BuyerMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=3, max_length=500)


app = FastAPI(
    title="CartPilot API",
    description="Safe agentic commerce API for electronics accessories.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/shop",
    response_model=CommerceAgentResult,
)
def shop(request: BuyerMessageRequest) -> CommerceAgentResult:
    try:
        return run_commerce_agent(request.message)

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="The AI intent service is temporarily unavailable.",
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="The shopping request could not be processed safely.",
        ) from exc