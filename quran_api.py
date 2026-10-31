"""
quran_api.py — مصدر البيانات: api.quran.com (v4)
الرواية: حفص عن عاصم — الرسم العثماني
"""

import json
import os
import threading
import time
import urllib.request
from typing import Optional

_CACHE_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quran_cache")
_META_FILE  = os.path.join(_CACHE_DIR, "chapters.json")
_SURAH_FILE = lambda n: os.path.join(_CACHE_DIR, f"surah_{n:03d}.json")

_BASE    = "https://api.quran.com/api/v4"
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
_TIMEOUT = 15

_chapters:    list = []
_surah_cache: dict = {}
_ready             = False
_lock              = threading.Lock()


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode())


def _parse_chapters(raw: list) -> list:
    out = []
    for ch in raw:
        out.append({
            "number":     ch["id"],
            "name_ar":    ch.get("name_arabic", ch.get("name_simple", "")),
            "name_en":    ch.get("translated_name", {}).get("name", ch.get("name_simple", "")),
            "ayah_count": ch["verses_count"],
        })
    return out


def _load_chapters() -> list:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    if os.path.exists(_META_FILE):
        try:
            with open(_META_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if data:
                return _parse_chapters(data)
        except Exception:
            pass
    try:
        raw  = _get(f"{_BASE}/chapters?language=ar")
        data = raw.get("chapters", [])
        with open(_META_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return _parse_chapters(data)
    except Exception as exc:
        print(f"[quran_api] chapters fetch failed: {exc}")
        return []


def _fetch_surah_from_api(number: int) -> Optional[list]:
    try:
        url  = f"{_BASE}/quran/verses/uthmani?chapter_number={number}"
        raw  = _get(url)
        vers = raw.get("verses", [])
        ayahs = []
        for v in vers:
            vk   = v.get("verse_key", "")
            ref  = vk.split(":")[-1] if ":" in vk else ""
            anum = int(ref) if ref.isdigit() else 0
            text = v.get("text_uthmani", "")
            ayahs.append({"number": anum, "ref": ref, "text": text})
        return ayahs if ayahs else None
    except Exception as exc:
        print(f"[quran_api] surah {number} fetch failed: {exc}")
        return None


def _load_surah(number: int) -> Optional[dict]:
    path = _SURAH_FILE(number)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    with _lock:
        meta = next((c for c in _chapters if c["number"] == number), None)
    if not meta:
        return None

    ayahs = _fetch_surah_from_api(number)
    if not ayahs:
        return None

    surah = {
        "number":     number,
        "name_ar":    meta["name_ar"],
        "name_en":    meta["name_en"],
        "ayah_count": meta["ayah_count"],
        "ayahs":      ayahs,
    }
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(surah, f, ensure_ascii=False)
    except Exception as exc:
        print(f"[quran_api] cache write: {exc}")
    return surah


def _init():
    global _chapters, _ready
    chs = _load_chapters()
    with _lock:
        _chapters = chs
        _ready    = bool(chs)
    if chs:
        print(f"[quran_api] ✅  {len(chs)} chapters ready.")
    else:
        print("[quran_api] ⚠️  Could not load chapter list.")

threading.Thread(target=_init, daemon=True).start()


# ── Public API ────────────────────────────────────────────────────────────────

def is_data_ready() -> bool:
    with _lock:
        return _ready

def total_ayahs() -> int:
    with _lock:
        return sum(c["ayah_count"] for c in _chapters)

def get_all_surahs() -> list:
    """Returns immediately — empty list if API not ready yet."""
    with _lock:
        return list(_chapters)

def get_surah(number: int) -> Optional[dict]:
    with _lock:
        if number in _surah_cache:
            return _surah_cache[number]
    # Wait for chapters to load if needed (called on user action, not startup)
    for _ in range(100):
        with _lock:
            if _chapters:
                break
        time.sleep(0.1)
    surah = _load_surah(number)
    if surah:
        with _lock:
            _surah_cache[number] = surah
    return surah

def clear_surah_cache(number: int = None):
    with _lock:
        if number is None:
            _surah_cache.clear()
        else:
            _surah_cache.pop(number, None)
    if number is None:
        import glob
        for p in glob.glob(os.path.join(_CACHE_DIR, "surah_*.json")):
            try: os.remove(p)
            except Exception: pass
    else:
        path = _SURAH_FILE(number)
        if os.path.exists(path):
            try: os.remove(path)
            except Exception: pass