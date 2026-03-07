import os
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(title="Radio Web Prototype")

BASE_DIR = Path(__file__).parent
RADIO_DIR = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = RADIO_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

if str(RADIO_DIR) not in sys.path:
    sys.path.insert(0, str(RADIO_DIR))

from brain import run_radio_command

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")


class CommandRequest(BaseModel):
    command: str = Field(min_length=1)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/web-config")
def web_config() -> Dict[str, Any]:
    spotify_web_client_id = os.getenv("SPOTIFY_WEB_CLIENT_ID", "").strip() or os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    return {
        "spotify_web_client_id": spotify_web_client_id,
    }


@app.post("/api/radio/command")
def handle_command(body: CommandRequest) -> Dict[str, Any]:
    return run_radio_command(body.command)
