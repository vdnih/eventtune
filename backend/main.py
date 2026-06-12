from dotenv import load_dotenv
load_dotenv()

import firebase_admin
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from routers import execute, generate, ingest

settings = get_settings()

firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})

app = FastAPI(title="Marketing Mail Generator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generate.router)
app.include_router(ingest.router)
app.include_router(execute.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
