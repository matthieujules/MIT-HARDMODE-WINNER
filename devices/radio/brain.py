import base64
import concurrent.futures
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Literal

#when this have to connect to the raspberry pi it needs to accept central command from the central command 

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

RADIO_DIR = Path(__file__).parent
OUTPUT_DIR = RADIO_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DIAL_STEP_DEGREES = 55

load_dotenv(RADIO_DIR / ".env")


class PodcastClip(BaseModel):
    text: str
    delivery_style: str = "neutral podcast"
    instructions: str = "Speak like a warm podcast host."
    voice: str = "nova"


class RadioPlan(BaseModel):
    action: Literal["output_music", "output_podcast"]
    turn_radio: bool = True
    spotify_query: str = ""
    spotify_market: str = "US"
    spotify_limit: int = Field(default=1, ge=1, le=5)
    podcast_clips: List[PodcastClip] = Field(default_factory=list)


def fallback_plan(command: str) -> RadioPlan:
    lower = command.lower()
    if any(word in lower for word in ["podcast", "news", "talk", "story"]):
        return RadioPlan(
            action="output_podcast",
            turn_radio=True,
            podcast_clips=[
                PodcastClip(
                    text="Welcome back to today's home update",
                    delivery_style="cheerful podcast",
                    instructions="Speak like a cheerful podcast host. Warm, natural, conversational, fast pace.",
                    voice="marin",
                ),
                PodcastClip(
                    text="Main story now from your smart home",
                    delivery_style="serious podcast",
                    instructions="Speak like a thoughtful podcast host. Warm, natural, conversational, medium pace.",
                    voice="onyx",
                ),
                PodcastClip(
                    text="That wraps this segment for now",
                    delivery_style="fun podcast",
                    instructions="Speak like a witty podcast host. Conversational and playful, quick pace.",
                    voice="fable",
                ),
            ],
        )

    cleaned = command.strip() or "chill lo-fi"
    return RadioPlan(
        action="output_music",
        turn_radio=True,
        spotify_query=cleaned,
        spotify_market="US",
        spotify_limit=1,
    )


def normalize_podcast_clips(plan: RadioPlan) -> RadioPlan:
    if plan.action != "output_podcast":
        return plan

    normalized = list(plan.podcast_clips)

    if len(normalized) > 4:
        normalized = normalized[:4]

    while len(normalized) < 2:
        if normalized:
            base_clip = normalized[-1]
            next_text = "Next short update from your radio"
            normalized.append(
                PodcastClip(
                    text=next_text,
                    delivery_style=base_clip.delivery_style,
                    instructions=base_clip.instructions,
                    voice=base_clip.voice or "nova",
                )
            )
        else:
            normalized.append(
                PodcastClip(
                    text="Quick update from your home radio",
                    delivery_style="neutral podcast",
                    instructions="Speak like a concise podcast host. Warm and conversational, fast pace.",
                    voice="nova",
                )
            )

    for clip in normalized:
        style = (clip.delivery_style or "").strip()
        clip.delivery_style = style if style.endswith(" podcast") else f"{style or 'neutral'} podcast"
        if not clip.instructions.strip():
            clip.instructions = f"Speak like a {clip.delivery_style} podcast host. Warm, natural, conversational, fast pace."

    plan.podcast_clips = normalized
    return plan


def normalize_voice(voice: str, default_voices: List[str]) -> str:
    alias_map = {
        "male": "onyx",
        "man": "onyx",
        "female": "nova",
        "woman": "nova",
        "robot": "echo",
        "robotic": "echo",
        "funny": "fable",
        "calm": "sage",
        "child": "coral",
    }
    lowered = (voice or "").strip().lower()
    mapped = alias_map.get(lowered, lowered)
    return mapped if mapped in default_voices else ""


def openai_plan_call(command: str) -> RadioPlan:
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_plan_model = os.getenv("OPENAI_PLAN_MODEL", "gpt-4.1-mini").strip()

    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY is missing")

    system_prompt = (
        "You are the radio planning model. "
        "Return only a valid JSON object that strictly matches the provided schema."
    )
    user_prompt = (
        "Plan one radio action from this command.\n"
        "Rules:\n"
        "- action must be output_music or output_podcast\n"
        "- turn_radio must always be true\n"
        "- if output_music: fill spotify_query, spotify_market, spotify_limit\n"
        "- if output_podcast: return 2-4 clips\n"
        "- each podcast clip text must be between 5 and 8 words\n"
        "- each podcast clip must include delivery_style, instructions, and voice\n"
        "- delivery_style must be adjective + ' podcast' (examples: fun podcast, sad podcast, serious podcast)\n"
        "- voice must be one of: alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer, verse, marin, cedar\n"
        "- when possible, vary male and female voices across clips\n"
        "- instructions must be different per clip and describe how to speak that clip\n"
        f"Command: {command}"
    )

    plan_schema = {
        "name": "radio_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": ["output_music", "output_podcast"]},
                "turn_radio": {"type": "boolean"},
                "spotify_query": {"type": "string"},
                "spotify_market": {"type": "string"},
                "spotify_limit": {"type": "integer", "minimum": 1, "maximum": 5},
                "podcast_clips": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string", "minLength": 5, "maxLength": 80},
                            "delivery_style": {"type": "string"},
                            "instructions": {"type": "string"},
                            "voice": {
                                "type": "string",
                                "enum": [
                                    "alloy",
                                    "ash",
                                    "ballad",
                                    "coral",
                                    "echo",
                                    "fable",
                                    "onyx",
                                    "nova",
                                    "sage",
                                    "shimmer",
                                    "verse",
                                    "marin",
                                    "cedar"
                                ]
                            },
                        },
                        "required": ["text", "delivery_style", "instructions", "voice"],
                    },
                },
            },
            "required": [
                "action",
                "turn_radio",
                "spotify_query",
                "spotify_market",
                "spotify_limit",
                "podcast_clips",
            ],
        },
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": openai_plan_model,
                "temperature": 0.2,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": plan_schema,
                },
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        response.raise_for_status()
        data = response.json()

    message = ((data.get("choices") or [{}])[0]).get("message", {})
    content = message.get("content", "")
    plan_json = json.loads(content)
    return RadioPlan.model_validate(plan_json)


def llm_call(command: str) -> RadioPlan:
    try:
        return normalize_podcast_clips(openai_plan_call(command))
    except Exception:
        return normalize_podcast_clips(fallback_plan(command))


def output_music(plan: RadioPlan) -> Dict[str, Any]:
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

    payload: Dict[str, Any] = {
        "function": "output_music",
        "turn_radio": plan.turn_radio,
        "dial_events": [
            {
                "event": "music_function_called",
                "degrees": DIAL_STEP_DEGREES,
            }
        ],
        "spotify_fields": {
            "query": plan.spotify_query,
            "market": plan.spotify_market,
            "limit": plan.spotify_limit,
        },
    }

    if not client_id or not client_secret:
        payload["spotify_api"] = "skipped_missing_credentials"
        payload["playback"] = {
            "type": "music",
            "status": "playing_simulated",
            "message": "Playing music (simulated). Add Spotify keys to stream previews.",
            "audio_url": None,
            "dial_events": payload["dial_events"],
        }
        return payload

    token_url = "https://accounts.spotify.com/api/token"
    search_url = "https://api.spotify.com/v1/search"
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")

    try:
        with httpx.Client(timeout=20.0) as client:
            token_resp = client.post(
                token_url,
                headers={"Authorization": f"Basic {basic}"},
                data={"grant_type": "client_credentials"},
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            search_resp = client.get(
                search_url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "q": plan.spotify_query,
                    "type": "track",
                    "market": plan.spotify_market,
                    "limit": max(plan.spotify_limit, 20),
                },
            )
            search_resp.raise_for_status()
            search_data = search_resp.json()

        tracks = search_data.get("tracks", {}).get("items") or []
        first_track = tracks[0] if tracks else None
        preview_track = next((track for track in tracks if track and track.get("preview_url")), None)
        selected_track = preview_track or first_track
        preview_url = selected_track.get("preview_url") if selected_track else None
        preview_candidates = len([track for track in tracks if track and track.get("preview_url")])

        payload["spotify_api"] = "ok"
        payload["track"] = selected_track
        payload["playback"] = {
            "type": "music",
            "status": "playing" if preview_url else "no_audio_available",
            "message": "Playing track preview in browser." if preview_url else "No Spotify preview URL found in search results, so browser cannot play music audio.",
            "audio_url": preview_url,
            "preview_tracks_found": preview_candidates,
            "dial_events": payload["dial_events"],
        }
        return payload
    except Exception as exc:
        payload["spotify_api"] = f"error: {exc}"
        payload["playback"] = {
            "type": "music",
            "status": "error",
            "message": f"Spotify error: {exc}",
            "audio_url": None,
            "dial_events": payload["dial_events"],
        }
        return payload


def output_podcast(plan: RadioPlan) -> Dict[str, Any]:
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_tts_model = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts").strip()
    default_voices = ["alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer", "verse", "marin", "cedar"]
    male_voices = ["ash", "echo", "fable", "onyx", "cedar", "verse"]
    female_voices = ["alloy", "ballad", "coral", "nova", "sage", "shimmer", "marin"]

    payload: Dict[str, Any] = {
        "function": "output_podcast",
        "turn_radio": plan.turn_radio,
        "clips_requested": [clip.model_dump() for clip in plan.podcast_clips],
        "clips_generated": [],
        "audio_items": [],
        "dial_events": [],
    }

    if not openai_api_key:
        payload["openai_tts_api"] = "skipped_missing_credentials"
        payload["playback"] = {
            "type": "podcast",
            "status": "playing_simulated",
            "message": "Playing podcast (simulated). Add OpenAI key for real clips.",
            "audio_queue": [],
            "dial_events": [
                {
                    "event": "tts_function_called",
                    "clip_index": idx + 1,
                    "degrees": DIAL_STEP_DEGREES,
                }
                for idx in range(len(plan.podcast_clips))
            ],
        }
        return payload

    headers = {
        "Authorization": f"Bearer {openai_api_key}",
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    request_id = int(time.time() * 1000)
    start_gender = random.choice(["male", "female"])

    clip_jobs = []
    for idx, clip in enumerate(plan.podcast_clips, start=1):
        planned_voice = normalize_voice(clip.voice, default_voices)
        target_gender = start_gender if idx % 2 == 1 else ("female" if start_gender == "male" else "male")
        gender_pool = male_voices if target_gender == "male" else female_voices
        selected_voice = planned_voice if planned_voice else random.choice(gender_pool)
        clip_jobs.append((idx, clip, selected_voice))
        payload["dial_events"].append(
            {
                "event": "tts_function_called",
                "clip_index": idx,
                "degrees": DIAL_STEP_DEGREES,
            }
        )

    def synthesize_clip(job: tuple[int, PodcastClip, str]) -> Dict[str, Any]:
        idx, clip, selected_voice = job
        out_file = OUTPUT_DIR / f"podcast_{request_id}_{idx}.mp3"
        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers=headers,
                    json={
                        "model": openai_tts_model,
                        "voice": selected_voice,
                        "input": clip.text,
                        "instructions": clip.instructions,
                        "speed": 1.4,
                        "response_format": "mp3",
                    },
                )
                resp.raise_for_status()

            out_file.write_bytes(resp.content)
            return {
                "index": idx,
                "text": clip.text,
                "delivery_style": clip.delivery_style,
                "instructions": clip.instructions,
                "voice": clip.voice,
                "selected_voice": selected_voice,
                "audio_url": f"/output/{out_file.name}",
                "audio_file": str(out_file),
            }
        except Exception as exc:
            return {
                "index": idx,
                "text": clip.text,
                "delivery_style": clip.delivery_style,
                "instructions": clip.instructions,
                "voice": clip.voice,
                "selected_voice": selected_voice,
                "error": str(exc),
            }

    max_workers = min(4, max(1, len(clip_jobs)))
    results_by_index: Dict[int, Dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(synthesize_clip, job): job[0] for job in clip_jobs}
        for future in concurrent.futures.as_completed(future_map):
            result = future.result()
            results_by_index[result["index"]] = result

    for idx in sorted(results_by_index.keys()):
        result = results_by_index[idx]
        payload["clips_generated"].append(result)
        if result.get("audio_url"):
            payload["audio_items"].append(
                {
                    "index": idx,
                    "audio_url": result["audio_url"],
                    "audio_file": result["audio_file"],
                }
            )

    payload["openai_tts_api"] = "ok"
    payload["playback"] = {
        "type": "podcast",
        "status": "playing" if payload["clips_generated"] else "playing_no_audio",
        "message": "Playing generated podcast clips in browser.",
        "audio_queue": [clip["audio_url"] for clip in payload["clips_generated"] if clip.get("audio_url")],
        "audio_items": payload["audio_items"],
        "dial_events": payload["dial_events"],
    }
    return payload


def run_radio_command(command: str) -> Dict[str, Any]:
    plan = llm_call(command)

    if plan.action == "output_music":
        execution = output_music(plan)
    else:
        execution = output_podcast(plan)

    return {
        "plan": plan.model_dump(),
        "execution": execution,
    }
