"""Nhà cung cấp LLM cho fallback trích xuất hóa đơn.
Mặc định: qwencoder (tương thích OpenAI). Cấu hình qua env:
LLM_BASE_URL, LLM_API_KEY (hoặc OPENAI_API_KEY), LLM_MODEL."""
import json
import os
import re
from abc import ABC, abstractmethod


def _load_dotenv():
    """Đọc .env (nếu có) — stdlib, không cần python-dotenv. Env đã set được ưu tiên."""
    try:
        path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


_load_dotenv()


class LLMProvider(ABC):
    """Lớp cơ sở cho các nhà cung cấp LLM."""

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Gửi prompt và trả về nội dung."""


class OpenAIProvider(LLMProvider):
    """Provider tương thích OpenAI — mặc định qwen3.7-max trên qwencoder."""

    def __init__(self):
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.qwencoder.cloud/api/v1")
        self.api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("LLM_MODEL", "qwen3.7-max")

    def complete(self, system: str, user: str) -> str:
        from openai import OpenAI
        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        r = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        # qwencoder trả JSON kèm "data: [DONE]" → SDK trả str thay vì object
        if isinstance(r, str):
            raw = re.sub(r"\s*data: \[DONE\].*$", "", r, flags=re.S).strip()
            return json.loads(raw)["choices"][0]["message"]["content"] or ""
        return r.choices[0].message.content or ""


class MockProvider(LLMProvider):
    """Provider giả lập cho test — trả JSON cố định."""

    def __init__(self, response: str = "{}"):
        self.response = response

    def complete(self, system: str, user: str) -> str:
        return self.response


def get_llm_provider():
    """Provider thật nếu có key, ngược lại None (fallback tắt)."""
    if os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"):
        return OpenAIProvider()
    return None