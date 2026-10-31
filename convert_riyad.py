"""
convert_riyad.py
Run this ONCE to convert "Ryiad Al Salheen.csv" → data/riyad.json

Usage:
    python convert_riyad.py
"""

import csv
import json
import os

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Ryiad Al Salheen.csv")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "riyad.json")


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    chapters: dict[str, dict] = {}

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            chapter_id = row.get("chapterId", "").strip()
            chapter_ar = row.get("chapter_ar", "").strip()
            hadith_id  = row.get("idInBook", "").strip()
            matin_ar   = row.get("matin_ar", "").strip()

            if not chapter_id or not matin_ar:
                continue

            if chapter_id not in chapters:
                chapters[chapter_id] = {
                    "number":   int(chapter_id),
                    "title_ar": chapter_ar,
                    "hadiths":  [],
                }

            try:
                num = int(hadith_id)
            except ValueError:
                num = len(chapters[chapter_id]["hadiths"]) + 1

            chapters[chapter_id]["hadiths"].append({
                "number":  num,
                "text_ar": matin_ar,
            })

    # Sort chapters by number
    result = sorted(chapters.values(), key=lambda c: c["number"])
    total  = sum(len(c["hadiths"]) for c in result)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

    size_kb = os.path.getsize(OUT_PATH) // 1024
    print(f"[convert_riyad] ✓ Saved {OUT_PATH}")
    print(f"[convert_riyad]   {len(result)} chapters, {total} hadiths, {size_kb} KB")


if __name__ == "__main__":
    main()