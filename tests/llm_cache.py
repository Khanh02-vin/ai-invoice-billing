"""LLM provider có cache JSONL cho benchmark — chạy lại offline, không gọi API 2 lần.
Cache key = sha1(system + user prompt). File cache được commit → số liệu reproducible
trên máy khác/CI mà không cần API key."""
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.base import OpenAIProvider
from src.extract.extractor import _LLM_SYSTEM

CACHE_DIR = Path(__file__).parent / "data"


class CachingProvider:
    """Bọc OpenAIProvider: hit cache → trả ngay; miss → gọi API + append JSONL."""

    def __init__(self, cache_path: Path, inner=None):
        self.inner = inner or OpenAIProvider()
        self.cache_path = Path(cache_path)
        self.cache = {}
        if self.cache_path.exists():
            for line in self.cache_path.open(encoding="utf-8"):
                d = json.loads(line)
                self.cache[d["h"]] = d["raw"]

    @staticmethod
    def _h(system: str, user: str) -> str:
        return hashlib.sha1((system + "\x00" + user).encode()).hexdigest()

    def complete(self, system: str, user: str) -> str:
        h = self._h(system, user)
        if h not in self.cache:
            raw = self.inner.complete(system, user)
            self.cache[h] = raw
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"h": h, "raw": raw}, ensure_ascii=False) + "\n")
        return self.cache[h]

    def preload(self, texts, workers: int = 8) -> int:
        """Gọi song song trước các text chưa có trong cache (prompt y hệt _extract_llm).
        Trả số call API thật đã thực hiện."""
        pairs = [(_LLM_SYSTEM, f"Hóa đơn:\n{t[:3000]}") for t in texts]
        missing = [(s, u) for s, u in pairs if self._h(s, u) not in self.cache]
        if not missing:
            return 0
        results = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self.inner.complete, s, u): (s, u) for s, u in missing}
            for i, fut in enumerate(futs):
                try:
                    results.append((futs[fut], fut.result()))
                except Exception as e:
                    results.append((futs[fut], json.dumps({"_error": str(e)[:100]})))
                if (len(results) % 50) == 0:
                    print(f"  preload {len(results)}/{len(missing)}...", flush=True)
        with self.cache_path.open("a", encoding="utf-8") as f:
            for (s, u), raw in results:
                h = self._h(s, u)
                self.cache[h] = raw
                f.write(json.dumps({"h": h, "raw": raw}, ensure_ascii=False) + "\n")
        return len(missing)
