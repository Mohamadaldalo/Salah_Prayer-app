"""
download_quran.py
=================
Run this ONCE before building your APK:

    python download_quran.py

It downloads the complete Quran (Arabic, quran-uthmani edition) from
alquran.cloud and saves it as  quran_data.json  in the same folder.

That file is then bundled inside the APK so all users get the full
Quran with no internet needed.
"""

import urllib.request
import json
import os
import sys
import time

OUT_FILE = "quran_data.json"
API_URL  = "https://api.alquran.cloud/v1/quran/quran-uthmani"

SURAH_NAMES_AR = {
    1: "الفاتحة", 2: "البقرة", 3: "آل عمران", 4: "النساء",
    5: "المائدة", 6: "الأنعام", 7: "الأعراف", 8: "الأنفال",
    9: "التوبة", 10: "يونس", 11: "هود", 12: "يوسف",
    13: "الرعد", 14: "إبراهيم", 15: "الحجر", 16: "النحل",
    17: "الإسراء", 18: "الكهف", 19: "مريم", 20: "طه",
    21: "الأنبياء", 22: "الحج", 23: "المؤمنون", 24: "النور",
    25: "الفرقان", 26: "الشعراء", 27: "النمل", 28: "القصص",
    29: "العنكبوت", 30: "الروم", 31: "لقمان", 32: "السجدة",
    33: "الأحزاب", 34: "سبأ", 35: "فاطر", 36: "يس",
    37: "الصافات", 38: "ص", 39: "الزمر", 40: "غافر",
    41: "فصلت", 42: "الشورى", 43: "الزخرف", 44: "الدخان",
    45: "الجاثية", 46: "الأحقاف", 47: "محمد", 48: "الفتح",
    49: "الحجرات", 50: "ق", 51: "الذاريات", 52: "الطور",
    53: "النجم", 54: "القمر", 55: "الرحمن", 56: "الواقعة",
    57: "الحديد", 58: "المجادلة", 59: "الحشر", 60: "الممتحنة",
    61: "الصف", 62: "الجمعة", 63: "المنافقون", 64: "التغابن",
    65: "الطلاق", 66: "التحريم", 67: "الملك", 68: "القلم",
    69: "الحاقة", 70: "المعارج", 71: "نوح", 72: "الجن",
    73: "المزمل", 74: "المدثر", 75: "القيامة", 76: "الإنسان",
    77: "المرسلات", 78: "النبأ", 79: "النازعات", 80: "عبس",
    81: "التكوير", 82: "الانفطار", 83: "المطففين", 84: "الانشقاق",
    85: "البروج", 86: "الطارق", 87: "الأعلى", 88: "الغاشية",
    89: "الفجر", 90: "البلد", 91: "الشمس", 92: "الليل",
    93: "الضحى", 94: "الشرح", 95: "التين", 96: "العلق",
    97: "القدر", 98: "البينة", 99: "الزلزلة", 100: "العاديات",
    101: "القارعة", 102: "التكاثر", 103: "العصر", 104: "الهمزة",
    105: "الفيل", 106: "قريش", 107: "الماعون", 108: "الكوثر",
    109: "الكافرون", 110: "النصر", 111: "المسد", 112: "الإخلاص",
    113: "الفلق", 114: "الناس",
}

SURAH_NAMES_EN = {
    1: "Al-Fatihah", 2: "Al-Baqarah", 3: "Aali Imran", 4: "An-Nisa",
    5: "Al-Maidah", 6: "Al-Anam", 7: "Al-Araf", 8: "Al-Anfal",
    9: "At-Tawbah", 10: "Yunus", 11: "Hud", 12: "Yusuf",
    13: "Ar-Rad", 14: "Ibrahim", 15: "Al-Hijr", 16: "An-Nahl",
    17: "Al-Isra", 18: "Al-Kahf", 19: "Maryam", 20: "Taha",
    21: "Al-Anbiya", 22: "Al-Hajj", 23: "Al-Muminun", 24: "An-Nur",
    25: "Al-Furqan", 26: "Ash-Shuara", 27: "An-Naml", 28: "Al-Qasas",
    29: "Al-Ankabut", 30: "Ar-Rum", 31: "Luqman", 32: "As-Sajdah",
    33: "Al-Ahzab", 34: "Saba", 35: "Fatir", 36: "Ya-Sin",
    37: "As-Saffat", 38: "Sad", 39: "Az-Zumar", 40: "Ghafir",
    41: "Fussilat", 42: "Ash-Shura", 43: "Az-Zukhruf", 44: "Ad-Dukhan",
    45: "Al-Jathiyah", 46: "Al-Ahqaf", 47: "Muhammad", 48: "Al-Fath",
    49: "Al-Hujurat", 50: "Qaf", 51: "Adh-Dhariyat", 52: "At-Tur",
    53: "An-Najm", 54: "Al-Qamar", 55: "Ar-Rahman", 56: "Al-Waqiah",
    57: "Al-Hadid", 58: "Al-Mujadila", 59: "Al-Hashr", 60: "Al-Mumtahanah",
    61: "As-Saf", 62: "Al-Jumuah", 63: "Al-Munafiqun", 64: "At-Taghabun",
    65: "At-Talaq", 66: "At-Tahrim", 67: "Al-Mulk", 68: "Al-Qalam",
    69: "Al-Haqqah", 70: "Al-Maarij", 71: "Nuh", 72: "Al-Jinn",
    73: "Al-Muzzammil", 74: "Al-Muddaththir", 75: "Al-Qiyamah", 76: "Al-Insan",
    77: "Al-Mursalat", 78: "An-Naba", 79: "An-Naziat", 80: "Abasa",
    81: "At-Takwir", 82: "Al-Infitar", 83: "Al-Mutaffifin", 84: "Al-Inshiqaq",
    85: "Al-Buruj", 86: "At-Tariq", 87: "Al-Ala", 88: "Al-Ghashiyah",
    89: "Al-Fajr", 90: "Al-Balad", 91: "Ash-Shams", 92: "Al-Layl",
    93: "Ad-Duha", 94: "Ash-Sharh", 95: "At-Tin", 96: "Al-Alaq",
    97: "Al-Qadr", 98: "Al-Bayyinah", 99: "Az-Zalzalah", 100: "Al-Adiyat",
    101: "Al-Qariah", 102: "At-Takathur", 103: "Al-Asr", 104: "Al-Humazah",
    105: "Al-Fil", 106: "Quraysh", 107: "Al-Maun", 108: "Al-Kawthar",
    109: "Al-Kafirun", 110: "An-Nasr", 111: "Al-Masad", 112: "Al-Ikhlas",
    113: "Al-Falaq", 114: "An-Nas",
}


def to_arabic_numerals(n: int) -> str:
    eastern = "٠١٢٣٤٥٦٧٨٩"
    return "".join(eastern[int(d)] for d in str(n))


def fetch_full_quran():
    print("📥 Downloading full Quran from alquran.cloud ...")
    print("   This is a one-time download (~2 MB). Please wait.\n")

    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(
                API_URL,
                headers={"User-Agent": "QuranApp/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)

            if data.get("status") != "OK":
                raise ValueError(f"API returned status: {data.get('status')}")

            return data["data"]["surahs"]

        except Exception as ex:
            print(f"   Attempt {attempt}/3 failed: {ex}")
            if attempt < 3:
                print("   Retrying in 3 seconds...")
                time.sleep(3)

    return None


def build_quran_json(surahs_raw):
    """
    Restructure raw API data into a clean, compact format:
    {
      "surahs": [
        {
          "number": 1,
          "name_ar": "الفاتحة",
          "name_en": "Al-Fatihah",
          "ayah_count": 7,
          "ayahs": [
            { "number": 1, "text": "بِسْمِ اللَّهِ ...", "ref": "١:١" },
            ...
          ]
        },
        ...
      ]
    }
    """
    quran = {"surahs": []}

    for s in surahs_raw:
        num = s["number"]
        ayahs = []

        for a in s["ayahs"]:
            ayah_num = a["numberInSurah"]
            ayahs.append({
                "number": ayah_num,
                "text":   a["text"],
                "ref":    f"{to_arabic_numerals(num)}:{to_arabic_numerals(ayah_num)}",
            })

        quran["surahs"].append({
            "number":     num,
            "name_ar":    SURAH_NAMES_AR.get(num, s.get("name", "")),
            "name_en":    SURAH_NAMES_EN.get(num, s.get("englishName", "")),
            "ayah_count": len(ayahs),
            "ayahs":      ayahs,
        })

    return quran


def main():
    if os.path.exists(OUT_FILE):
        size_kb = os.path.getsize(OUT_FILE) // 1024
        print(f"✅ {OUT_FILE} already exists ({size_kb} KB). Nothing to do.")
        print("   Delete it and re-run if you want to re-download.")
        return

    surahs_raw = fetch_full_quran()

    if not surahs_raw:
        print("\n❌ Download failed after 3 attempts.")
        print("   Check your internet connection and try again.")
        sys.exit(1)

    print("✅ Download complete! Building JSON structure...")
    quran = build_quran_json(surahs_raw)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(quran, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(OUT_FILE) // 1024
    total_ayahs = sum(s["ayah_count"] for s in quran["surahs"])
    print(f"✅ Saved {OUT_FILE}  ({size_kb} KB, {len(quran['surahs'])} surahs, {total_ayahs} ayahs)")
    print("\n🎉 Done! quran_data.json is ready to be bundled with your app.")


if __name__ == "__main__":
    main()
