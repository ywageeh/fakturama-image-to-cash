"""Provider-agnostic LLM client: anything that speaks the OpenAI chat-completions API
with image input (OpenAI, Groq vision models, OpenRouter, Gemini's OpenAI endpoint, Ollama, ...)."""

import base64
import json
import mimetypes
from pathlib import Path

from openai import OpenAI

from .config import Config


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    def json(self, system: str, text: str, image: Path) -> dict:
        """One chat call in JSON mode with the image attached."""
        mime = mimetypes.guess_type(image)[0] or "image/png"
        b64 = base64.b64encode(image.read_bytes()).decode()
        resp = self.client.chat.completions.create(
            model=self.cfg.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return json.loads(resp.choices[0].message.content)
