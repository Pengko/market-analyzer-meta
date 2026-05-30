import json
import os
import time

import requests

_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8320/v1")
_API_KEY = os.getenv("LLM_API_KEY", "")
_MODEL = os.getenv("LLM_MODEL", "nemotron-free")


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        start = raw.find("\n")
        end = raw.rfind("```")
        if start != -1 and end != -1:
            raw = raw[start:end].strip()
    return raw


def llm_judge(task: str, context: dict, temperature: float = 0.3, timeout: int | None = None) -> dict:
    if timeout is None:
        timeout = int(os.getenv("LLM_TIMEOUT", "60"))
    max_retries = int(os.getenv("LLM_RETRIES", "3"))
    retry_delay = int(os.getenv("LLM_RETRY_DELAY", "2"))

    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {_API_KEY}"},
                json={
                    "model": _MODEL,
                    "messages": [
                        {"role": "system", "content": task},
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                    ],
                    "temperature": temperature,
                },
                timeout=timeout,
            )
            data = resp.json()
            if "choices" in data and data["choices"]:
                content = _extract_json(data["choices"][0]["message"]["content"])
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    raise RuntimeError(f"LLM returned invalid JSON: {content[:200]}")
            err = data.get("error", data)
            err_msg = json.dumps(err, ensure_ascii=False)[:200]
        except requests.exceptions.ReadTimeout:
            err_msg = "Read timed out"
        except requests.exceptions.ConnectionError:
            err_msg = "Connection error"

        if attempt < max_retries - 1:
            time.sleep(retry_delay * (attempt + 1))
            last_error = err_msg
        else:
            raise RuntimeError(f"LLM failed after {max_retries} attempts: {err_msg}")
