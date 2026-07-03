from dotenv import load_dotenv
load_dotenv()

import firebase_admin
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from routers import data, events, integration, marketing, spaces, threads

settings = get_settings()

firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})

app = FastAPI(title="EventTune API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(spaces.router)
app.include_router(integration.router)
app.include_router(marketing.router)
app.include_router(threads.router)
app.include_router(events.router)
app.include_router(data.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
