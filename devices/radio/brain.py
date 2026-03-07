from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RADIO_DIR = Path(__file__).resolve().parent
SOUNDS_DIR = RADIO_DIR / "Sounds"

MUSIC_CODES = ("A", "B", "C", "D", "E", "F", "G")


@dataclass(frozen=True)
class AudioChoice:
	code: str
	path: Path
	kind: str  # "music" | "soundbite" | "glitch"
	label: str


@dataclass(frozen=True)
class SelectionDecision:
	final_token: str
	llm_raw_output: str | None
	llm_token: str | None
	source: str
	llm_called: bool


def _load_choices() -> tuple[dict[str, AudioChoice], AudioChoice | None]:
	choices: dict[str, AudioChoice] = {}
	glitch: AudioChoice | None = None

	if not SOUNDS_DIR.exists():
		return choices, glitch

	for path in sorted(SOUNDS_DIR.glob("*.mp3"), key=lambda item: item.name.lower()):
		stem = path.stem

		if re.match(r"^00(?:_|$)", stem):
			label = re.sub(r"^00_?", "", stem).strip() or "Glitch"
			glitch = AudioChoice(code="00", path=path, kind="glitch", label=label)
			continue

		music_match = re.match(r"^([A-G])_", stem)
		if music_match:
			code = music_match.group(1)
			if code in MUSIC_CODES and code not in choices:
				label = re.sub(r"^[A-G]_", "", stem).replace("_", " ").strip()
				choices[code] = AudioChoice(code=code, path=path, kind="music", label=label)
			continue

		bite_match = re.match(r"^(\d{1,2})_", stem)
		if bite_match:
			code = bite_match.group(1)
			if code != "00" and code not in choices:
				label = re.sub(r"^\d{1,2}_", "", stem).replace("_", " ").strip()
				choices[code] = AudioChoice(code=code, path=path, kind="soundbite", label=label)

	return choices, glitch


def _is_stop_request(request: str) -> bool:
	return bool(re.search(r"\b(stop|pause|hold)\b", request, flags=re.IGNORECASE))


def _is_explicit_music_request(request: str) -> bool:
	return bool(re.search(r"\b(song|music)\b", request, flags=re.IGNORECASE))


def _phrase_match_score(request_text: str, phrase_text: str) -> float:
	request_terms = _tokenize_text(request_text)
	phrase_terms = _tokenize_text(phrase_text)
	if not request_terms or not phrase_terms:
		return 0.0
	overlap = len(request_terms.intersection(phrase_terms))
	return overlap / len(phrase_terms)


def _sanitize_llm_token(raw: str, allowed: set[str]) -> str | None:
	text = (raw or "").strip().upper()
	if not text:
		return None

	if re.search(r"\bSTOP\b", text):
		return "stop"

	if text in allowed:
		return text

	for allowed_token in allowed:
		if allowed_token.isdigit() and text == str(int(allowed_token)):
			return allowed_token

	for token in re.findall(r"[A-Z0-9]+", text):
		if token.isdigit():
			for allowed_token in allowed:
				if allowed_token.isdigit() and int(token) == int(allowed_token):
					return allowed_token
		if token in allowed:
			return token

	for match in re.finditer(r"\b[A-G]\b|\b\d{1,2}\b", text):
		token = match.group(0)
		if token.isdigit():
			for allowed_token in allowed:
				if allowed_token.isdigit() and int(token) == int(allowed_token):
					return allowed_token
		if token in allowed:
			return token

	return None


def _extract_output_text(response: Any) -> str:
	output_text = getattr(response, "output_text", None)
	if isinstance(output_text, str) and output_text.strip():
		return output_text.strip()

	output = getattr(response, "output", None)
	if not output:
		return ""

	chunks: list[str] = []
	for item in output:
		content = getattr(item, "content", None) or []
		for piece in content:
			text_value = getattr(piece, "text", None)
			if text_value:
				chunks.append(str(text_value))
	return "\n".join(chunks).strip()


def _tokenize_text(text: str) -> set[str]:
	return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token}


REQUEST_TERM_ALIASES: dict[str, set[str]] = {
	"energetic": {"cheerful", "happy", "rage", "anger", "funny", "workout", "hype"},
	"workout": {"energetic", "rage", "anger", "hype"},
	"happy": {"cheerful", "funny", "happy"},
	"sad": {"sad"},
	"scary": {"scary", "thriller"},
	"romantic": {"romantic", "romatic", "love"},
	"calm": {"classic", "chill", "lofi"},
	"chill": {"classic", "calm", "lofi"},
	"lofi": {"chill", "calm", "classic"},
}


def _expand_request_terms(request_terms: set[str]) -> set[str]:
	expanded = set(request_terms)
	for term in request_terms:
		expanded.update(REQUEST_TERM_ALIASES.get(term, set()))
	return expanded


def _stable_request_index(request: str, count: int) -> int:
	if count <= 0:
		return 0
	value = sum(ord(char) for char in request)
	return value % count


def _choose_code_by_metadata(request: str, allowed_codes: list[str], choices: dict[str, AudioChoice]) -> str:
	if not allowed_codes:
		return "1"

	request_terms = _expand_request_terms(_tokenize_text(request))

	if ("energetic" in request_terms or "workout" in request_terms or "hype" in request_terms) and "B" in allowed_codes:
		return "B"
	if ("happy" in request_terms or "cheerful" in request_terms) and "E" in allowed_codes:
		return "E"
	if ("scary" in request_terms or "thriller" in request_terms) and "C" in allowed_codes:
		return "C"
	if "sad" in request_terms and "F" in allowed_codes:
		return "F"
	if ("romantic" in request_terms or "romatic" in request_terms or "love" in request_terms) and "G" in allowed_codes:
		return "G"

	scored: list[tuple[int, str]] = []
	for code in allowed_codes:
		choice = choices.get(code)
		if choice is None:
			scored.append((0, code))
			continue
		label_terms = _expand_request_terms(_tokenize_text(choice.label))
		score = len(request_terms.intersection(label_terms))
		scored.append((score, code))

	best_score = max(score for score, _ in scored)
	best_codes = [code for score, code in scored if score == best_score]
	if best_score > 0 and best_codes:
		return best_codes[0]

	return allowed_codes[_stable_request_index(request, len(allowed_codes))]


def _phrase_override_token(request: str, choices: dict[str, AudioChoice]) -> str | None:
	text = re.sub(r"\s+", " ", request.lower()).strip()

	if not _is_stop_request(text) and re.search(r"\b(hi|hello|hey)\b", text):
		if "09" in choices:
			return "09"

	rules = [
		("omg my date is here", "19"),
		("guys you gotta help me", "07"),
		("interesting house you've got", "03"),
		("interesting house you’ve got", "03"),
	]

	for phrase, token in rules:
		if (phrase in text or _phrase_match_score(text, phrase) >= 0.66) and token in choices:
			return token

	return None


def _choose_code_with_llm(request: str, allowed_codes: list[str], choices: dict[str, AudioChoice]) -> SelectionDecision:
	fallback = _choose_code_by_metadata(request, allowed_codes, choices)
	allowed = set(allowed_codes)

	api_key = os.getenv("OPENAI_API_KEY", "").strip()
	if not api_key:
		return SelectionDecision(
			final_token=fallback,
			llm_raw_output=None,
			llm_token=None,
			source="fallback:no_api_key",
			llm_called=False,
		)

	try:
		from openai import OpenAI  # type: ignore
	except Exception:
		return SelectionDecision(
			final_token=fallback,
			llm_raw_output=None,
			llm_token=None,
			source="fallback:openai_import_error",
			llm_called=False,
		)

	model = os.getenv("OPENAI_PLAN_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
	options_text = "\n".join(
		f"- {code}: {choices.get(code).label if choices.get(code) else 'unknown option'}"
		for code in allowed_codes
	)
	system_prompt = (
		"You route radio requests to one exact token. "
		"Return ONLY one token in strict format: an UPPERCASE letter A-G, or a numeric clip token (for example 03, 07, 19). "
		"Do not return lowercase letters, punctuation, JSON, or extra words."
	)
	user_prompt = (
		f"Request: {request}\n"
		"Available options from the Sounds folder:\n"
		f"{options_text}\n"
		f"Allowed tokens (strict): {', '.join(allowed_codes)}\n"
		"Special mappings:\n"
		"- if request means 'Omg my date is here' (or close paraphrase) -> 19\n"
		"- if request means 'Guys you gotta help me' (or close paraphrase) -> 07\n"
		"- if request means 'Interesting House you've got' (or close paraphrase) -> 03\n"
		"- if request means 'hi/hello/hey' -> 09\n"
		"Output STOP for explicit stop intents: stop, pause, hold.\n"
		"Output rule: respond with exactly one token only, no explanation.\n"
		"Examples of valid outputs: A, C, 03, 07, 19, STOP"
	)

	client = OpenAI(api_key=api_key)

	response_errors: list[str] = []

	try:
		response = client.responses.create(
			model=model,
			temperature=0,
			max_output_tokens=8,
			input=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_prompt},
			],
		)
		text = _extract_output_text(response)
		parsed = _sanitize_llm_token(text, allowed)
		if parsed is not None:
			return SelectionDecision(
				final_token=parsed,
				llm_raw_output=text,
				llm_token=parsed,
				source="llm:responses",
				llm_called=True,
			)
		return SelectionDecision(
			final_token=fallback,
			llm_raw_output=text or "[empty-output]",
			llm_token=None,
			source="fallback:invalid_llm_output",
			llm_called=True,
		)
	except Exception as exc:
		response_errors.append(f"responses:{exc}")

	try:
		completion = client.chat.completions.create(
			model=model,
			temperature=0,
			max_tokens=8,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_prompt},
			],
		)
		text = ((completion.choices[0].message.content or "") if completion.choices else "").strip()
		parsed = _sanitize_llm_token(text, allowed)
		if parsed is not None:
			return SelectionDecision(
				final_token=parsed,
				llm_raw_output=text,
				llm_token=parsed,
				source="llm:chat_completions",
				llm_called=True,
			)
		return SelectionDecision(
			final_token=fallback,
			llm_raw_output=text or "[empty-output]",
			llm_token=None,
			source="fallback:invalid_llm_output",
			llm_called=True,
		)
	except Exception as exc:
		response_errors.append(f"chat:{exc}")

	error_text = " | ".join(response_errors) if response_errors else "unknown"
	return SelectionDecision(
		final_token=fallback,
		llm_raw_output=f"[llm-error] {error_text}",
		llm_token=None,
		source="fallback:llm_exception",
		llm_called=True,
	)


def select_audio_decision(request: str) -> SelectionDecision:
	"""
	Returns exactly one token for a scenario/situation request:
	- "stop"
	- "1".."5" for sound bites
	- "A".."G" for music
	"""
	text = (request or "").strip()
	if not text:
		return SelectionDecision(
			final_token="1",
			llm_raw_output=None,
			llm_token=None,
			source="fallback:empty_request",
			llm_called=False,
		)

	if _is_stop_request(text):
		return SelectionDecision(
			final_token="stop",
			llm_raw_output="STOP",
			llm_token="stop",
			source="direct:stop_keyword",
			llm_called=False,
		)

	choices, _ = _load_choices()
	wants_music = _is_explicit_music_request(text)

	override_token = _phrase_override_token(text, choices)
	if override_token is not None:
		return SelectionDecision(
			final_token=override_token,
			llm_raw_output=f"[phrase-rule] {override_token}",
			llm_token=override_token,
			source="rule:phrase_override",
			llm_called=False,
		)

	if wants_music:
		allowed_pool = list(MUSIC_CODES)
	else:
		numeric_codes = sorted(
			[code for code, choice in choices.items() if choice.kind == "soundbite" and code.isdigit()],
			key=lambda item: int(item),
		)
		allowed_pool = numeric_codes

	allowed_codes = [code for code in allowed_pool if code in choices]
	if not allowed_codes:
		allowed_codes = list(allowed_pool)

	decision = _choose_code_with_llm(text, allowed_codes, choices)
	if decision.final_token == "stop" and not _is_stop_request(text):
		fallback = _choose_code_by_metadata(text, allowed_codes, choices)
		return SelectionDecision(
			final_token=fallback,
			llm_raw_output=decision.llm_raw_output,
			llm_token=decision.llm_token,
			source="fallback:blocked_non_explicit_stop",
			llm_called=decision.llm_called,
		)

	return decision


def _clip_entry(index: int, choice: AudioChoice) -> dict[str, Any]:
	relative_file = f"Sounds/{choice.path.name}"
	return {
		"index": index,
		"token": choice.code,
		"kind": choice.kind,
		"label": choice.label,
		"file": relative_file,
		"audio_file": relative_file,
		"audio_url": None,
		"text": choice.label,
	}


def run_radio_command(command: str) -> dict[str, Any]:
	request = (command or "").strip()
	choices, glitch = _load_choices()

	if _is_stop_request(request):
		stop_clips: list[dict[str, Any]] = []
		if glitch is not None:
			stop_clips.append(_clip_entry(1, glitch))
		return {
			"ok": True,
			"command": request,
			"selection": "stop",
			"plan": {
				"action": "stop_audio",
				"turn_radio": False,
				"selection": "stop",
			},
			"execution": {
				"llm_called": False,
				"llm_decision": "STOP",
				"llm_token": "stop",
				"final_selection": "stop",
				"selection_source": "direct:stop_keyword",
				"stop_requested": True,
				"clips_generated": stop_clips,
				"playback": {
					"type": "stop",
					"status": "stop_requested",
					"audio_items": stop_clips,
					"audio_queue": [],
				},
			},
		}

	pre_llm_glitch = _clip_entry(1, glitch) if glitch is not None else None
	decision = select_audio_decision(request)
	token = decision.final_token

	if token == "stop":
		stop_clips = [pre_llm_glitch] if pre_llm_glitch else []
		return {
			"ok": True,
			"command": request,
			"selection": "stop",
			"plan": {
				"action": "stop_audio",
				"turn_radio": False,
				"selection": "stop",
			},
			"execution": {
				"llm_called": decision.llm_called,
				"llm_decision": decision.llm_raw_output,
				"llm_token": decision.llm_token,
				"final_selection": "stop",
				"selection_source": decision.source,
				"stop_requested": True,
				"pre_llm_glitch": pre_llm_glitch,
				"clips_generated": stop_clips,
				"playback": {
					"type": "stop",
					"status": "stop_requested",
					"audio_items": stop_clips,
					"audio_queue": [],
				},
			},
		}

	selected = choices.get(token)
	if selected is None:
		wants_music = _is_explicit_music_request(request)
		if wants_music:
			fallback_codes = list(MUSIC_CODES)
		else:
			fallback_codes = sorted(
				[code for code, choice in choices.items() if choice.kind == "soundbite" and code.isdigit()],
				key=lambda item: int(item),
			)
		selected = next((choices.get(code) for code in fallback_codes if code in choices), None)
		if selected is None:
			return {
				"ok": False,
				"error": "No matching audio files found in Sounds folder for the required naming format.",
				"command": request,
				"selection": token,
			}

	clips: list[dict[str, Any]] = []
	next_index = 1
	if pre_llm_glitch is not None:
		clips.append(pre_llm_glitch)
		next_index += 1

	clips.append(_clip_entry(next_index, selected))
	next_index += 1

	if glitch is not None:
		clips.append(_clip_entry(next_index, glitch))

	playback_type = "music" if selected.kind == "music" else "podcast"
	return {
		"ok": True,
		"command": request,
		"selection": selected.code,
		"plan": {
			"action": "output_podcast",
			"turn_radio": True,
			"selection": selected.code,
			"category": selected.kind,
			"requires_explicit_music_keyword": True,
		},
		"execution": {
			"llm_called": decision.llm_called,
			"llm_decision": decision.llm_raw_output,
			"llm_token": decision.llm_token,
			"final_selection": selected.code,
			"selection_source": decision.source,
			"pre_llm_glitch": pre_llm_glitch,
			"post_clip_glitch": _clip_entry(next_index, glitch) if glitch is not None else None,
			"clips_generated": clips,
			"playback": {
				"type": playback_type,
				"status": "queued_local_files",
				"audio_items": clips,
				"audio_queue": [],
				"message": "Glitch plays before LLM return path and after selected clip.",
			},
		},
	}
