from fastapi import APIRouter

from app.api.companies import router as companies_router
from app.api.filings import router as filings_router
from app.api.metrics import router as metrics_router
from app.api.research import router as research_agent_router
from app.api.retrieval import router as retrieval_router

api_router = APIRouter(prefix="/api")
api_router.include_router(companies_router)
api_router.include_router(filings_router)
api_router.include_router(metrics_router)
api_router.include_router(retrieval_router)
api_router.include_router(research_agent_router)
