# Radio Web Prototype

Minimal localhost test app for radio decisioning with 3 core functions:

1. `llm_call(command)`
2. `output_music(plan)` (Spotify fields + turn signal + volume)
3. `output_podcast(plan)` (multiple short OpenAI TTS clips + turn signal + volume)

The shared radio brain lives in `devices/radio/brain.py` (outside `web`).

## Run

```bash
cd devices/radio/web
pip install -r requirements.txt
uvicorn app:app --reload --port 8010
```

Open: http://127.0.0.1:8010

## Environment

Use `devices/radio/.env` (template: `devices/radio/.env.example`) and fill in keys if you want live API calls:

- `ANTHROPIC_API_KEY`
- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `OPENAI_API_KEY`
- `OPENAI_PLAN_MODEL`
- `OPENAI_TTS_MODEL`

If keys are missing, the app still runs in fallback/mock mode.
