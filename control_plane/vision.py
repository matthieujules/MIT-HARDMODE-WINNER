"""Claude Vision analysis for ClaudeHome.

Receives base64 JPEG frames, calls Claude Vision API for scene analysis,
compares against current state, and triggers master reasoning if significant changes detected.
"""

import json
import logging
import os

from anthropic import Anthropic

logger = logging.getLogger(__name__)

_VISION_MODEL = os.getenv("VISION_MODEL", "claude-sonnet-4-6")

VISION_PROMPT = """Analyze this camera frame from a smart home's top-down room camera.
Return JSON only:
{
  "people_count": <number of people visible>,
  "people": [
    {
      "x_pct": <0-100>,
      "y_pct": <0-100>,
      "confidence": <0.0-1.0>,
      "description": "<brief descriptor like primary user, guest, person at door>"
    }
  ],
  "mood": "<happy|sad|stressed|tired|neutral|focused|relaxed>",
  "mood_confidence": <0.0-1.0>,
  "activity": "<brief description of what's happening>",
  "notable": "<anything unusual, or null>",
  "user_position": {"x_pct": <0-100>, "y_pct": <0-100>}
}
The x_pct/y_pct values represent approximate positions as percentage of frame dimensions (0,0 = top-left).
Always include every visible person in "people". Keep "user_position" as the primary person's position for backward compatibility.
"""


def analyze_frame(image_b64: str) -> dict | None:
    """Call Claude Vision API with a base64 JPEG frame.
    Returns parsed analysis dict, or None on error."""
    try:
        client = Anthropic()
        response = client.messages.create(
            model=_VISION_MODEL,
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }],
        )
        # Parse JSON from response
        text = response.content[0].text.strip()
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as e:
        logger.error("Vision analysis failed: %s", e)
        return None


def should_trigger_master(analysis: dict, current_state: dict) -> bool:
    """Decide if this vision analysis warrants triggering master reasoning.

    Triggers only on meaningful scene changes (people entering/leaving),
    not noisy signals like mood fluctuations.
    """
    if analysis is None:
        return False

    detected_people_count = analysis.get("people_count")
    if detected_people_count is None and isinstance(analysis.get("people"), list):
        detected_people_count = len(analysis["people"])

    if (detected_people_count is not None
            and detected_people_count != current_state.get("people_count")):
        return True

    return False
