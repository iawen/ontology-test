"""Standalone application entry point for electrical drawing recognition.

Run from the ``src`` directory with:
``python -m drawing_recognition.main``
"""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from drawing_recognition.api import router


app = FastAPI(
    title="Electrical Drawing Recognition",
    version="0.1.0",
    description="Vector-first electrical DWG/DXF component recognition service.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "drawing-recognition", "version": app.version}


app.include_router(router, tags=["Drawing Recognition"])


if __name__ == "__main__":
    uvicorn.run("drawing_recognition.main:app", host="0.0.0.0", port=8001, reload=True)