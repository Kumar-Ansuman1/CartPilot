from backend.app.delegated_api import router as delegated_router
from backend.app.main import app


app.include_router(delegated_router)
