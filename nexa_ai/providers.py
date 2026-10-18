"""Provider adapters with ordered fallback: Clean APIs -> Gemini -> Groq -> OpenRouter."""
import base64
import json
import httpx
from . import config

SYSTEM_PROMPT = (
    "You are Nexa AI, a helpful personal AI assistant. Be conversational and useful. "
    "Use the conversation context when answering. If an attachment is provided, analyze its content "
    "rather than claiming you cannot see it. Be honest about uncertainty. "
    "For Telegram replies, put source code, terminal commands, Git/deployment commands, JSON, configuration, environment-variable examples, and prompts meant to be copied into fenced code blocks with an appropriate language label. Keep ordinary explanations outside code blocks."
)

class ProviderError(Exception):
    pass

def configured_providers():
    result = []
    if config.CLEANAPI_API_KEY:
        result.append(("Clean APIs", "cleanapi"))
    if config.GEMINI_API_KEY:
        result.append(("Gemini", "gemini"))
    if config.GROQ_API_KEY:
        result.append(("Groq", "groq"))
    if config.OPENROUTER_API_KEY:
        result.append(("OpenRouter", "openrouter"))
    return result

def _model(provider):
    return {
        "cleanapi": config.CLEANAPI_MODEL,
        "gemini": config.GEMINI_MODEL,
        "groq": config.GROQ_MODEL,
        "openrouter": config.OPENROUTER_MODEL,
    }[provider]

def _base(provider):
    return {
        "cleanapi": config.CLEANAPI_BASE_URL,
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }[provider]

async def _openai_compatible(provider, history, user_text, attachment=None):
    key = {
        "cleanapi": config.CLEANAPI_API_KEY,
        "groq": config.GROQ_API_KEY,
        "openrouter": config.OPENROUTER_API_KEY,
    }[provider]
    model = _model(provider)
    if not model:
        if provider == "cleanapi":
            # Clean APIs model IDs can vary by account. Ask their API for models.
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(_base(provider) + "/models", headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                data = r.json().get("data", [])
                if not data:
                    raise ProviderError("Clean APIs returned no models. Set CLEANAPI_MODEL in environment.")
                model = data[0].get("id")
        else:
            raise ProviderError(f"No model configured for {provider}. Set its model environment variable.")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history:
        messages.append({"role": item["role"], "content": item["content"]})
    if attachment and attachment.get("kind") == "image":
        data_url = f"data:{attachment['mime']};base64,{base64.b64encode(attachment['bytes']).decode()}"
        content = [{"type": "text", "text": user_text or "Please analyze this image."},
                   {"type": "image_url", "image_url": {"url": data_url}}]
    else:
        content = user_text
    messages.append({"role": "user", "content": content})
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://telegram.org/"
        headers["X-Title"] = "Nexa AI Telegram"
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
        response = await client.post(_base(provider) + "/chat/completions",
                                     headers=headers,
                                     json={"model": model, "messages": messages, "temperature": 0.7})
    if response.status_code >= 400:
        body = response.text[:500]
        raise ProviderError(f"{provider} HTTP {response.status_code}: {body}")
    data = response.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"{provider} returned an unexpected response.")
    if isinstance(text, list):
        text = "\n".join(part.get("text", "") for part in text if isinstance(part, dict))
    if not text:
        raise ProviderError(f"{provider} returned an empty response.")
    return str(text), model

async def _gemini(history, user_text, attachment=None):
    key, model = config.GEMINI_API_KEY, config.GEMINI_MODEL
    contents = []
    for item in history:
        contents.append({"role": "model" if item["role"] == "assistant" else "user",
                         "parts": [{"text": item["content"]}]})
    parts = [{"text": user_text or ("Please analyze this attachment." if attachment else "")}]
    if attachment and attachment.get("kind") == "image":
        parts.append({"inline_data": {"mime_type": attachment["mime"],
                                     "data": base64.b64encode(attachment["bytes"]).decode()}})
    elif attachment and attachment.get("kind") == "pdf":
        # Gemini supports inline PDF bytes for supported models.
        parts.append({"inline_data": {"mime_type": "application/pdf",
                                     "data": base64.b64encode(attachment["bytes"]).decode()}})
    contents.append({"role": "user", "parts": parts})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
        response = await client.post(url, params={"key": key},
            json={"system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                  "contents": contents,
                  "generationConfig": {"temperature": 0.7}})
    if response.status_code >= 400:
        raise ProviderError(f"Gemini HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise ProviderError("Gemini returned no text. The model may not support this input.")
    if not text.strip():
        raise ProviderError("Gemini returned an empty response.")
    return text, model

async def answer(history, user_text, attachment=None):
    providers = configured_providers()
    if not providers:
        raise ProviderError("No AI provider is configured. Add at least one API key to your environment.")
    errors = []
    for name, provider in providers:  # fixed preference order
        try:
            if provider == "gemini":
                result, model = await _gemini(history, user_text, attachment)
            else:
                # Do not pretend a scanned PDF was analyzed if this adapter does not
                # explicitly transmit its binary contents. Try the next provider instead.
                if attachment and attachment.get("kind") == "pdf":
                    raise ProviderError("This adapter path does not send raw PDF bytes; trying a provider with PDF input support.")
                # Images are sent as data URLs; unsupported models/providers will fail
                # and the ordered fallback loop will continue.
                if attachment and attachment.get("kind") == "image":
                    result, model = await _openai_compatible(provider, history, user_text, attachment)
                else:
                    result, model = await _openai_compatible(provider, history, user_text)
            return result, name, model
        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:240]}")
    raise ProviderError("All configured providers failed in the selected order:\n" + "\n".join(errors))
