"""
download_riyad.py
Run this ONCE to download Riyad al-Salihin and save to data/riyad.json

Usage:
    python download_riyad.py
"""

import urllib.request
import json
import os

# The data is split across chapters in the repo — we fetch the full book file
URLS_TO_TRY = [
    "https://raw.githubusercontent.com/AhmedBaset/hadith-json/main/db/by_book/RyadSalihin.json",
    "https://raw.githubusercontent.com/zafhi/hadith-database/main/db/by_book/RyadSalihin.json",
    "https://raw.githubusercontent.com/AhmedBaset/hadith-json/main/db/by_book/riyad-us-salihin.json",
    "https://raw.githubusercontent.com/zafhi/hadith-database/main/db/by_book/riyad-us-salihin.json",
]

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "riyad.json")


def try_download():
    for url in URLS_TO_TRY:
        print(f"[download_riyad] Trying {url} ...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
            data = json.loads(raw)
            print(f"[download_riyad] ✓ Got data from {url}")
            return data
        except Exception as exc:
            print(f"[download_riyad] ✗ Failed: {exc}")
    return None


def normalize(data) -> list:
    """
    Normalize whatever structure we get into a clean list of chapters:
    [
      {
        "number": 1,
        "title_ar": "...",
        "hadiths": [
          {"number": 1, "text_ar": "..."},
          ...
        ]
      },
      ...
    ]
    """
    chapters = []

    # Structure A: list of chapters with hadiths inside
    if isinstance(data, list):
        for i, ch in enumerate(data, 1):
            hadiths = []
            raw_hadiths = ch.get("hadiths", ch.get("hadith", []))
            for j, h in enumerate(raw_hadiths, 1):
                text_ar = (
                    h.get("arabic", "")
                    or h.get("text_ar", "")
                    or h.get("body", "")
                    or h.get("text", "")
                )
                hadiths.append({
                    "number": h.get("id", j),
                    "text_ar": text_ar.strip(),
                })
            chapters.append({
                "number":   i,
                "title_ar": (
                    ch.get("title_ar", "")
                    or ch.get("arabic", "")
                    or ch.get("title", "")
                    or f"باب {i}"
                ),
                "hadiths": hadiths,
            })

    # Structure B: dict with chapters key
    elif isinstance(data, dict):
        raw = data.get("chapters", data.get("data", data.get("book", [])))
        return normalize(raw)

    return chapters


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    data = try_download()
    if not data:
        print("\n[download_riyad] ❌ All URLs failed. Check your internet connection.")
        return

    chapters = normalize(data)
    total_hadiths = sum(len(ch["hadiths"]) for ch in chapters)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(chapters, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(OUT_PATH) // 1024
    print(f"[download_riyad] ✓ Saved {OUT_PATH}")
    print(f"[download_riyad]   {len(chapters)} chapters, {total_hadiths} hadiths, {size_kb} KB")


if __name__ == "__main__":
    main()