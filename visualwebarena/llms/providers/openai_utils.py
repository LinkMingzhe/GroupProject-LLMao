# Copyright (c) Meta Platforms, Inc. and affiliates.
"""Tools to generate from OpenAI prompts.
Adopted from https://github.com/zeno-ml/zeno-build/"""

import asyncio
import logging
import os
import random
import time
from typing import Any

import aiolimiter
import openai
from openai import AsyncAzureOpenAI, AzureOpenAI
from openai import AsyncOpenAI, OpenAI

client = None
aclient = None
client_backend = None

DEFAULT_DASHSCOPE_BASE_URL = (
    "https://dashscope-intl.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1"
)


def _is_qwen_model(model_name: str | None) -> bool:
    return bool(model_name) and model_name.lower().startswith("qwen")


def _get_dashscope_base_url() -> str:
    return os.environ.get("DASHSCOPE_BASE_URL", DEFAULT_DASHSCOPE_BASE_URL)


def _qwen_enable_thinking() -> bool:
    raw_value = os.environ.get("QWEN_ENABLE_THINKING", "0").strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def _resolve_openai_backend(model_name: str | None) -> str:
    if _is_qwen_model(model_name):
        return "dashscope"
    if "AZURE_API_ENDPOINT" in os.environ and "AZURE_API_KEY" in os.environ:
        return "azure"
    if "OPENAI_API_BASE" in os.environ:
        return "openai_compatible"
    return "openai"


def _chat_completion_kwargs(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    top_p: float,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": top_p,
    }
    if _is_qwen_model(model):
        kwargs["extra_body"] = {"enable_thinking": _qwen_enable_thinking()}
    return kwargs


def _responses_input_part(part: Any) -> dict[str, Any]:
    if isinstance(part, str):
        return {"type": "input_text", "text": part}

    if not isinstance(part, dict):
        raise ValueError(
            f"Unsupported Qwen content part type: {part.__class__.__name__}"
        )

    part_type = part.get("type")
    if part_type == "text":
        return {"type": "input_text", "text": part.get("text", "")}
    if part_type == "image_url":
        image_url = part.get("image_url", {})
        if isinstance(image_url, dict):
            image_url_value = image_url.get("url")
            detail = image_url.get("detail")
        else:
            image_url_value = image_url
            detail = part.get("detail")
        if not image_url_value:
            raise ValueError("Qwen image input is missing image_url.url")
        converted_part: dict[str, Any] = {
            "type": "input_image",
            "image_url": image_url_value,
        }
        if detail:
            converted_part["detail"] = detail
        return converted_part

    raise ValueError(f"Unsupported Qwen content part type: {part_type}")


def _chat_messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    converted_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        if role not in {"user", "assistant", "system", "developer"}:
            role = "user"
        content = message.get("content", "")
        if isinstance(content, list):
            converted_content = [_responses_input_part(part) for part in content]
        else:
            converted_content = [_responses_input_part(content)]
        name = message.get("name")
        if name:
            name_prefix = f"[{name}]\n"
            if converted_content and converted_content[0].get("type") == "input_text":
                converted_content[0]["text"] = (
                    name_prefix + converted_content[0].get("text", "")
                )
            else:
                converted_content.insert(
                    0, {"type": "input_text", "text": name_prefix.rstrip()}
                )
        converted_messages.append(
            {
                "type": "message",
                "role": role,
                "content": converted_content,
            }
        )
    return converted_messages


def _responses_api_kwargs(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    top_p: float,
) -> dict[str, Any]:
    return {
        "model": model,
        "input": _chat_messages_to_responses_input(messages),
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "top_p": top_p,
        "extra_body": {"enable_thinking": _qwen_enable_thinking()},
    }


def _extract_completion_text(response: Any) -> str:
    if isinstance(response, dict):
        return response["choices"][0]["text"]
    return response.choices[0].text


def _extract_chat_response_text(response: Any) -> str:
    if isinstance(response, dict):
        return response["choices"][0]["message"]["content"]
    return response.choices[0].message.content


def _extract_responses_text(response: Any) -> str:
    if isinstance(response, dict):
        return response.get("output_text", "")

    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _ensure_openai_clients(model_name: str | None = None) -> None:
    global client, aclient, client_backend
    target_backend = _resolve_openai_backend(model_name)
    if (
        client is not None
        and aclient is not None
        and client_backend == target_backend
    ):
        return

    if target_backend == "azure":
        api_version = (
            "2024-10-21"
            if "AZURE_API_VERSION" not in os.environ
            else os.environ["AZURE_API_VERSION"]
        )
        client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_API_ENDPOINT"],
            api_key=os.environ["AZURE_API_KEY"],
            api_version=api_version,
        )
        aclient = AsyncAzureOpenAI(
            azure_endpoint=os.environ["AZURE_API_ENDPOINT"],
            api_key=os.environ["AZURE_API_KEY"],
            api_version=api_version,
        )
        client_backend = target_backend
        return

    if target_backend == "dashscope":
        if "DASHSCOPE_API_KEY" not in os.environ:
            raise ValueError(
                "DASHSCOPE_API_KEY environment variable must be set when using Qwen models."
            )
        base_url = _get_dashscope_base_url()
        client = OpenAI(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            base_url=base_url,
        )
        aclient = AsyncOpenAI(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            base_url=base_url,
        )
        client_backend = target_backend
        return

    if target_backend == "openai_compatible":
        # Used for running vllm models.
        print("WARNING: Using OPENAI_API_KEY=EMPTY")
        client = OpenAI(api_key="EMPTY", base_url=os.environ["OPENAI_API_BASE"])
        aclient = AsyncOpenAI(api_key="EMPTY", base_url=os.environ["OPENAI_API_BASE"])
        client_backend = target_backend
        return

    if "OPENAI_API_KEY" not in os.environ:
        raise ValueError(
            "either OPENAI_API_KEY or AZURE_API_KEY environment variable must be set when using OpenAI API."
        )

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    aclient = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    client_backend = target_backend


from tqdm.asyncio import tqdm_asyncio


def retry_with_exponential_backoff(  # type: ignore
    func,
    initial_delay: float = 1,
    exponential_base: float = 2,
    jitter: bool = True,
    max_retries: int = 3,
    errors: tuple[Any] = (
        openai.RateLimitError,
        openai.BadRequestError,
        openai.InternalServerError,
    ),
):
    """Retry a function with exponential backoff."""

    def wrapper(*args, **kwargs):  # type: ignore
        # Initialize variables
        num_retries = 0
        delay = initial_delay

        # Loop until a successful response or max_retries is hit or an exception is raised
        while True:
            try:

                return func(*args, **kwargs)

            # Retry on specified errors
            except errors as e:
                # Increment retries
                num_retries += 1
                print("Error while calling OpenAI API: ", e)
                # Check if max retries has been reached
                if num_retries > max_retries:
                    raise Exception(
                        f"Maximum number of retries ({max_retries}) exceeded."
                    )

                # Increment the delay
                delay *= exponential_base * (1 + jitter * random.random())

                # Sleep for the delay
                time.sleep(delay)

            # Raise exceptions for any errors not specified
            except Exception as e:
                raise e

    return wrapper


async def _throttled_openai_completion_acreate(
    engine: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    limiter: aiolimiter.AsyncLimiter,
) -> dict[str, Any]:
    async with limiter:
        if _is_qwen_model(engine):
            raise ValueError("Qwen models are only supported in chat mode.")
        for _ in range(3):
            try:
                return await aclient.completions.create(
                    engine=engine,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                )
            except openai.RateLimitError:
                logging.warning(
                    "OpenAI API rate limit exceeded. Sleeping for 10 seconds."
                )
                await asyncio.sleep(10)
            except openai.APIError as e:
                logging.warning(f"OpenAI API error: {e}")
                break
        return {"choices": [{"message": {"content": ""}}]}


async def agenerate_from_openai_completion(
    prompts: list[str],
    engine: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    requests_per_minute: int = 300,
) -> list[str]:
    """Generate from OpenAI Completion API.

    Args:
        prompts: list of prompts
        temperature: Temperature to use.
        max_tokens: Maximum number of tokens to generate.
        top_p: Top p to use.
        context_length: Length of context to use.
        requests_per_minute: Number of requests per minute to allow.

    Returns:
        List of generated responses.
    """
    _ensure_openai_clients(engine)

    limiter = aiolimiter.AsyncLimiter(requests_per_minute)
    async_responses = [
        _throttled_openai_completion_acreate(
            engine=engine,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            limiter=limiter,
        )
        for prompt in prompts
    ]
    responses = await tqdm_asyncio.gather(*async_responses)
    return [_extract_completion_text(x) for x in responses]


@retry_with_exponential_backoff
def generate_from_openai_completion(
    prompt: str,
    engine: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    stop_token: str | None = None,
) -> str:
    _ensure_openai_clients(engine)
    if _is_qwen_model(engine):
        raise ValueError("Qwen models are only supported in chat mode.")

    response = client.completions.create(
        prompt=prompt,
        engine=engine,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        stop=[stop_token],
    )
    answer: str = _extract_completion_text(response)
    return answer


async def _throttled_openai_chat_completion_acreate(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    top_p: float,
    limiter: aiolimiter.AsyncLimiter,
) -> dict[str, Any]:
    async with limiter:
        for _ in range(3):
            try:
                if _is_qwen_model(model):
                    return await aclient.responses.create(
                        **_responses_api_kwargs(
                            model=model,
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            top_p=top_p,
                        )
                    )
                return await aclient.chat.completions.create(
                    **_chat_completion_kwargs(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        top_p=top_p,
                    )
                )
            except openai.RateLimitError:
                logging.warning(
                    "OpenAI API rate limit exceeded. Sleeping for 10 seconds."
                )
                await asyncio.sleep(10)
            except asyncio.exceptions.TimeoutError:
                logging.warning("OpenAI API timeout. Sleeping for 10 seconds.")
                await asyncio.sleep(10)
            except openai.APIError as e:
                logging.warning(f"OpenAI API error: {e}")
                break
        if _is_qwen_model(model):
            return {"output_text": ""}
        return {"choices": [{"message": {"content": ""}}]}


async def agenerate_from_openai_chat_completion(
    messages_list: list[list[dict[str, Any]]],
    engine: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    requests_per_minute: int = 300,
) -> list[str]:
    """Generate from OpenAI Chat Completion API.

    Args:
        messages_list: list of message list
        temperature: Temperature to use.
        max_tokens: Maximum number of tokens to generate.
        top_p: Top p to use.
        context_length: Length of context to use.
        requests_per_minute: Number of requests per minute to allow.

    Returns:
        List of generated responses.
    """
    _ensure_openai_clients(engine)

    limiter = aiolimiter.AsyncLimiter(requests_per_minute)
    async_responses = [
        _throttled_openai_chat_completion_acreate(
            model=engine,
            messages=message,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            limiter=limiter,
        )
        for message in messages_list
    ]
    responses = await tqdm_asyncio.gather(*async_responses)
    if _is_qwen_model(engine):
        return [_extract_responses_text(x) for x in responses]
    return [_extract_chat_response_text(x) for x in responses]


@retry_with_exponential_backoff
def generate_from_openai_chat_completion(
    messages: list[dict[str, Any]],
    model: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    stop_token: str | None = None,
) -> str:
    if _resolve_openai_backend(model) == "openai_compatible":
        assert "llama" in model.lower()
    _ensure_openai_clients(model)
    if _is_qwen_model(model):
        response = client.responses.create(
            **_responses_api_kwargs(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
            )
        )
        answer: str = _extract_responses_text(response)
        return answer
    response = client.chat.completions.create(
        **_chat_completion_kwargs(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        )
    )
    answer: str = _extract_chat_response_text(response)
    return answer


@retry_with_exponential_backoff
# debug only
def fake_generate_from_openai_chat_completion(
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
    top_p: float,
    context_length: int,
    stop_token: str | None = None,
) -> str:
    _ensure_openai_clients()

    answer = "Let's think step-by-step. This page shows a list of links and buttons. There is a search box with the label 'Search query'. I will click on the search box to type the query. So the action I will perform is \"click [60]\"."
    return answer
