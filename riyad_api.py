"""
riyad_api.py — fully offline, zero network calls
Reads from data/riyad.json built by convert_riyad.py
"""

import json
import os
import threading

_BASE      = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_BASE, "data", "riyad.json")

_chapters:      list[dict] = []
_chapter_index: dict[int, dict] = {}
_ready:         bool = False
_lock           = threading.Lock()


def _load():
    global _chapters, _chapter_index, _ready
    if not os.path.exists(_JSON_PATH):
        print(f"[riyad_api] ⚠️  {_JSON_PATH} not found. Run: python convert_riyad.py")
        return
    try:
        with open(_JSON_PATH, encoding="utf-8") as f:
            data = json.load(f)
        index = {c["number"]: c for c in data}
        with _lock:
            _chapters      = data
            _chapter_index = index
            _ready         = True
        print(f"[riyad_api] ✓ Loaded {len(data)} chapters from local JSON")
    except Exception as exc:
        print(f"[riyad_api] ✗ Failed to load riyad.json: {exc}")


threading.Thread(target=_load, daemon=True).start()


def is_ready() -> bool:
    return _ready


def get_all_chapters() -> list[dict]:
    with _lock:
        return [{"number": c["number"], "title_ar": c["title_ar"],
                 "hadith_count": len(c["hadiths"])} for c in _chapters]


def get_chapter(number: int) -> dict | None:
    with _lock:
        return _chapter_index.get(number)


def total_hadiths() -> int:
    with _lock:
        return sum(len(c["hadiths"]) for c in _chapters)