import os
import sys
import importlib.util
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

app = FastAPI(title="Radio Web Prototype")
APP_BUILD = "radio-web-2026-03-07-llm-trace-v1"

BASE_DIR = Path(__file__).parent
RADIO_DIR = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = RADIO_DIR / "output"
SOUNDS_DIR = RADIO_DIR / "Sounds"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SOUNDS_DIR.mkdir(parents=True, exist_ok=True)

if str(RADIO_DIR) not in sys.path:
    sys.path.insert(0, str(RADIO_DIR))

if load_dotenv is not None:
    load_dotenv(RADIO_DIR / ".env")

_BRAIN_FILE = RADIO_DIR / "brain.py"
_brain_spec = importlib.util.spec_from_file_location("radio_brain_local", _BRAIN_FILE)
if _brain_spec is None or _brain_spec.loader is None:
    raise RuntimeError(f"Unable to load brain module from {_BRAIN_FILE}")
_brain_module = importlib.util.module_from_spec(_brain_spec)
sys.modules[_brain_spec.name] = _brain_module
_brain_spec.loader.exec_module(_brain_module)
run_radio_command = _brain_module.run_radio_command

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/Sounds", StaticFiles(directory=SOUNDS_DIR), name="sounds")


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
    result = run_radio_command(body.command)
    result["_app_build"] = APP_BUILD

    execution = result.setdefault("execution", {})
    llm_called = bool(execution.get("llm_called"))
    final_selection = execution.get("final_selection") or result.get("selection")

    execution["llm_decision"] = execution.get("llm_decision") or "[no-llm-output]"
    execution["llm_token"] = execution.get("llm_token") or "[none]"
    execution["final_selection"] = final_selection or "[none]"
    execution["selection_source"] = execution.get("selection_source") or (
        "llm" if llm_called and execution.get("llm_token") else ("fallback:unknown" if llm_called else "direct")
    )

    return result
