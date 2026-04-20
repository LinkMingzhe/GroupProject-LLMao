"""Tools to generate from Gemini prompts."""

import importlib
import os
import random
import threading
import time
from collections import deque
from io import BytesIO
from typing import Any

from PIL import Image as PILImage

try:
    from google import genai as genai_client
except Exception:
    genai_client = None

try:
    from google.api_core.exceptions import InvalidArgument
except Exception:
    InvalidArgument = Exception

try:
    from google.genai import errors as genai_errors
except Exception:
    genai_errors = None

try:
    from vertexai.preview.generative_models import (
        GenerativeModel,
        HarmBlockThreshold,
        HarmCategory,
        Image as VertexImage,
    )
except Exception:
    GenerativeModel = None
    HarmBlockThreshold = None
    HarmCategory = None
    VertexImage = None


_GEMINI_INPUT_TOKENS_PER_MINUTE = int(
    os.environ.get("GEMINI_INPUT_TOKENS_PER_MINUTE", "15000")
)
_GEMINI_RATE_LIMIT_WINDOW_SECONDS = 60.0
_GEMINI_REQUEST_HISTORY: deque[tuple[float, int]] = deque()
_GEMINI_RATE_LIMIT_LOCK = threading.Lock()


def retry_with_exponential_backoff(  # type: ignore
    func,
    initial_delay: float = 1,
    exponential_base: float = 1,
    jitter: bool = True,
    max_retries: int = 10,
    errors: tuple[Any] = (InvalidArgument,),
):
    """Retry a function with exponential backoff."""

    def wrapper(*args, **kwargs):  # type: ignore
        num_retries = 0
        delay = initial_delay

        while True:
            try:
                return func(*args, **kwargs)
            except errors:
                num_retries += 1
                if num_retries > max_retries:
                    raise Exception(
                        f"Maximum number of retries ({max_retries}) exceeded."
                    )
                delay *= exponential_base * (1 + jitter * random.random())
                time.sleep(delay)
            except Exception as e:
                raise e

    return wrapper


def _convert_prompt_to_genai(prompt: list[Any]) -> list[Any]:
    converted: list[Any] = []
    for item in prompt:
        if isinstance(item, str):
            converted.append(item)
        elif isinstance(item, PILImage.Image):
            converted.append(item)
        elif VertexImage is not None and isinstance(item, VertexImage):
            # Convert Vertex image payload to PIL for Google Generative AI SDK.
            img_bytes = item._image_bytes  # pyright: ignore[reportPrivateUsage]
            converted.append(PILImage.open(BytesIO(img_bytes)))
        else:
            raise TypeError(f"Unsupported Gemini prompt item type: {type(item)}")
    return converted


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return text
    if hasattr(response, "candidates"):
        chunks: list[str] = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", []) if content is not None else []
            for part in parts or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    chunks.append(part_text)
        if chunks:
            return "\n".join(chunks)
    raise ValueError("Gemini response did not contain text output.")


def _load_legacy_genai():
    try:
        return importlib.import_module("google.generativeai")
    except Exception:
        return None


def _prune_gemini_request_history(now: float) -> None:
    cutoff = now - _GEMINI_RATE_LIMIT_WINDOW_SECONDS
    while _GEMINI_REQUEST_HISTORY and _GEMINI_REQUEST_HISTORY[0][0] <= cutoff:
        _GEMINI_REQUEST_HISTORY.popleft()


def _sleep_until_gemini_budget_available(required_tokens: int) -> None:
    if required_tokens > _GEMINI_INPUT_TOKENS_PER_MINUTE:
        raise ValueError(
            "Single Gemini/Gemma request exceeds local per-minute token budget: "
            f"required={required_tokens}, limit={_GEMINI_INPUT_TOKENS_PER_MINUTE}. "
            "Reduce prompt size further before retrying."
        )

    while True:
        with _GEMINI_RATE_LIMIT_LOCK:
            now = time.time()
            _prune_gemini_request_history(now)
            used_tokens = sum(tokens for _, tokens in _GEMINI_REQUEST_HISTORY)
            if used_tokens + required_tokens <= _GEMINI_INPUT_TOKENS_PER_MINUTE:
                _GEMINI_REQUEST_HISTORY.append((now, required_tokens))
                return

            oldest_ts, _ = _GEMINI_REQUEST_HISTORY[0]
            wait_seconds = max(
                1.0, oldest_ts + _GEMINI_RATE_LIMIT_WINDOW_SECONDS - now + 0.25
            )

        print(
            f"[Gemini/Gemma rate limiter] used={used_tokens} "
            f"required={required_tokens} limit={_GEMINI_INPUT_TOKENS_PER_MINUTE}; "
            f"sleeping {wait_seconds:.1f}s"
        )
        time.sleep(wait_seconds)


def _estimate_gemini_input_tokens(client: Any, model: str, prompt: list[Any]) -> int:
    converted_prompt = _convert_prompt_to_genai(prompt)
    count_response = client.models.count_tokens(
        model=model,
        contents=converted_prompt,
    )
    total_tokens = getattr(count_response, "total_tokens", None)
    if total_tokens is None:
        raise ValueError("Gemini count_tokens response did not include total_tokens.")
    return int(total_tokens)


@retry_with_exponential_backoff
def generate_from_gemini_completion(
    prompt: list[Any],
    engine: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")

    if api_key:
        if genai_client is not None:
            client = genai_client.Client(api_key=api_key)
            converted_prompt = _convert_prompt_to_genai(prompt)
            required_tokens = _estimate_gemini_input_tokens(client, engine, prompt)
            _sleep_until_gemini_budget_available(required_tokens)
            try:
                response = client.models.generate_content(
                    model=engine,
                    contents=converted_prompt,
                    config={
                        "candidate_count": 1,
                        "max_output_tokens": max_tokens,
                        "top_p": top_p,
                        "temperature": temperature,
                    },
                )
            except Exception as e:
                if genai_errors is not None and isinstance(e, genai_errors.ClientError):
                    message = str(e)
                    if "429 RESOURCE_EXHAUSTED" in message:
                        print(
                            "[Gemini/Gemma rate limiter] upstream 429 despite local "
                            "budgeting; sleeping 60s before surfacing the error"
                        )
                        time.sleep(60)
                raise
            return _extract_response_text(response)
        legacy_genai = _load_legacy_genai()
        if legacy_genai is not None:
            legacy_genai.configure(api_key=api_key)
            model = legacy_genai.GenerativeModel(engine)
            response = model.generate_content(
                _convert_prompt_to_genai(prompt),
                generation_config={
                    "candidate_count": 1,
                    "max_output_tokens": max_tokens,
                    "top_p": top_p,
                    "temperature": temperature,
                },
            )
            return _extract_response_text(response)
        raise ImportError(
            "Gemini SDK missing. Install either `google-generativeai` or `google-genai` "
            "when GEMINI_API_KEY is set."
        )

    if GenerativeModel is None:
        raise EnvironmentError(
            "Gemini is not configured. Set GEMINI_API_KEY or configure Vertex AI SDK."
        )

    model = GenerativeModel(engine)
    safety_config = {
        HarmCategory.HARM_CATEGORY_UNSPECIFIED: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    }
    response = model.generate_content(
        prompt,
        generation_config=dict(
            candidate_count=1,
            max_output_tokens=max_tokens,
            top_p=top_p,
            temperature=temperature,
        ),
        safety_settings=safety_config,
    )
    return response.text


@retry_with_exponential_backoff
def fake_generate_from_gemini_chat_completion(
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    stop_token: str | None = None,
) -> str:
    del messages, model, temperature, max_tokens, top_p, context_length, stop_token
    answer = (
        'Let\'s think step-by-step. This page shows a list of links and buttons. '
        "There is a search box with the label 'Search query'. "
        'I will click on the search box to type the query. '
        'So the action I will perform is "click [60]".'
    )
    return answer
