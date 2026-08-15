from fastapi import APIRouter

from app.api.companies import router as companies_router
from app.api.filings import router as filings_router

api_router = APIRouter(prefix="/api")
api_router.include_router(companies_router)
api_router.include_router(filings_router)
