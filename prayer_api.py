import requests
from datetime import datetime

# =============================
# GLOBAL PRAYER METHODS
# =============================
PRAYER_METHODS = {
    "General (MWL)": 3,
    "Turkey (Diyanet)": 13,
    "UAE / Gulf (Umm Al-Qura)": 4,
    "Egypt": 5,
    "ISNA (North America)": 2
}

# =============================
# GET PRAYER TIMES
# =============================
def get_prayer_times(city, country, method_name):

    today = datetime.now().strftime("%d-%m-%Y")

    method_number = PRAYER_METHODS.get(method_name, 3)

    url = "https://api.aladhan.com/v1/timingsByCity"

    params = {
        "city": city.strip(),
        "country": country.strip(),
        "date": today,
        "method": method_number,
        "school": 0
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    data = response.json()
    timings = data["data"]["timings"]

    prayer_times = {
        "Fajr": timings["Fajr"][:5],
        "Dhuhr": timings["Dhuhr"][:5],
        "Asr": timings["Asr"][:5],
        "Maghrib": timings["Maghrib"][:5],
        "Isha": timings["Isha"][:5],
    }

    return prayer_times
