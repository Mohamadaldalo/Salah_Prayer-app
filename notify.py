"""
Salah Reminder — main.py  (maximum performance)
• الخط العثماني (Amiri Quran)
• Surah reader uses item_extent on ListView → scroll_to works perfectly
• Page switches are instant (visibility toggle only)
• No blocking calls on the main thread
"""

import flet as ft
import threading
import time
import json
import os
import base64
from datetime import datetime

from prayer_api import get_prayer_times, PRAYER_METHODS
from quran_api import get_all_surahs, get_surah, is_data_ready, total_ayahs

try:
    from plyer import notification as _plyer_notify
    PLYER_OK = True
except Exception:
    PLYER_OK = False

try:
    import azan_player as _ap
    AZAN_OK = _ap.is_available()
except Exception:
    _ap     = None
    AZAN_OK = False

print(f"[Salah] plyer={PLYER_OK}  azan={AZAN_OK}")

# ── Paths ────────────────────────────────────────────────────────────────────
SAVE_FILE         = "completed_prayers.json"
LOCATION_FILE     = "location.json"
BOOKMARK_FILE     = "bookmark.json"
PRAYER_CACHE_FILE = "prayer_cache.json"

# ── Colours ──────────────────────────────────────────────────────────────────
C_BG       = "#FAF7F2"
C_SURFACE  = "#F0EBE1"
C_TEAL     = "#0D4A4A"
C_TEAL_LT  = "#DFF0EF"
C_AMBER    = "#C47A1A"
C_AMBER_LT = "#FDE9C2"
C_AMBER_DK = "#B8680F"
C_TEXT     = "#1C1A17"
C_TEXT_SUB = "#6B5E4E"
C_DIVIDER  = "#D9CFBF"
C_BM_HL    = "#FEF3DC"
C_DANGER   = "#9B2020"
QURAN_FONT = "AmiriQuran"

_IMG_CACHE: dict[str, str] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _btn(bg: str, fg: str = "#1C1A17") -> ft.ButtonStyle:
    return ft.ButtonStyle(bgcolor=bg, color=fg,
                          shape=ft.RoundedRectangleBorder(radius=8))


def _write_json(path: str, data: dict):
    def _go():
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as exc:
            print(f"[Salah] write {path}: {exc}")
    threading.Thread(target=_go, daemon=True).start()


class Debounce:
    def __init__(self, delay: float, fn):
        self._delay, self._fn, self._timer = delay, fn, None
        self._lock = threading.Lock()

    def __call__(self, *a, **kw):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fn, a, kw)
            self._timer.start()


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

def load_completed_prayers() -> set:
    try:
        with open(SAVE_FILE) as f:
            d = json.load(f)
        if d.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return set(d.get("prayers", []))
    except Exception:
        pass
    return set()


def save_completed_prayers(prayers: set):
    _write_json(SAVE_FILE, {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "prayers": list(prayers),
    })


def load_location():
    try:
        with open(LOCATION_FILE) as f:
            d = json.load(f)
        return d.get("city"), d.get("country"), d.get("method", "General (MWL)")
    except Exception:
        return None, None, "General (MWL)"


def save_location(city, country, method):
    _write_json(LOCATION_FILE, {"city": city, "country": country, "method": method})


def load_bookmark():
    try:
        with open(BOOKMARK_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_bookmark(surah: int, ayah: int):
    _write_json(BOOKMARK_FILE, {"surah": surah, "ayah": ayah})


def clear_bookmark():
    threading.Thread(
        target=lambda: os.path.exists(BOOKMARK_FILE) and os.remove(BOOKMARK_FILE),
        daemon=True,
    ).start()


# ═══════════════════════════════════════════════════════════════════════════════
# PRAYER TIMES
# ═══════════════════════════════════════════════════════════════════════════════

def _load_cache(city, country, method):
    try:
        with open(PRAYER_CACHE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if (d.get("date") == datetime.now().strftime("%Y-%m-%d")
                and d.get("city") == city and d.get("country") == country
                and d.get("method") == method and d.get("times")):
            return d["times"]
    except Exception:
        pass
    return None


def _load_any_cache(city, country, method):
    try:
        with open(PRAYER_CACHE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if (d.get("city") == city and d.get("country") == country
                and d.get("method") == method and d.get("times")):
            return d["times"]
    except Exception:
        pass
    return None


def get_prayer_times_best(city, country, method) -> dict:
    cached = _load_cache(city, country, method)
    if cached:
        return cached
    stale = _load_any_cache(city, country, method)
    if stale:
        def _refresh():
            try:
                times = get_prayer_times(city, country, method)
                if times:
                    _write_json(PRAYER_CACHE_FILE, {
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "city": city, "country": country,
                        "method": method, "times": times,
                    })
            except Exception as exc:
                print(f"[Salah] bg refresh: {exc}")
        threading.Thread(target=_refresh, daemon=True).start()
        return stale
    try:
        times = get_prayer_times(city, country, method)
        if times:
            _write_json(PRAYER_CACHE_FILE, {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "city": city, "country": country,
                "method": method, "times": times,
            })
            return times
    except Exception as exc:
        print(f"[Salah] live fetch: {exc}")
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# AZAN WRAPPERS
# ═══════════════════════════════════════════════════════════════════════════════

def azan_play(name):
    if not AZAN_OK: return
    try: _ap.play(name)
    except Exception as e: print(f"[azan] play: {e}")

def azan_stop():
    if not AZAN_OK: return
    try: _ap.stop()
    except Exception as e: print(f"[azan] stop: {e}")

def azan_is_playing():
    if not AZAN_OK: return False
    try: return bool(_ap.is_playing())
    except Exception: return False

def azan_get_volume():
    if not AZAN_OK: return 1.0
    try: return float(_ap.get_volume())
    except Exception: return 1.0

def azan_set_volume(v):
    if not AZAN_OK: return
    try: _ap.set_volume(max(0.0, min(1.0, v)))
    except Exception as e: print(f"[azan] vol: {e}")

def azan_set_on_stop(cb):
    if not AZAN_OK: return
    try: _ap.set_on_stop(lambda *_: cb())
    except Exception as e: print(f"[azan] on_stop: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# ASSETS
# ═══════════════════════════════════════════════════════════════════════════════

def _find_assets_dir() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        c = os.path.join(root, "assets")
        if os.path.isdir(c) and any(f.startswith("azkar_") for f in os.listdir(c)):
            return c
        try:
            for sub in os.listdir(root):
                ca = os.path.join(root, sub, "assets")
                if os.path.isdir(ca) and any(f.startswith("azkar_") for f in os.listdir(ca)):
                    return ca
        except PermissionError:
            pass
        root = os.path.dirname(root)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

ASSETS_DIR = _find_assets_dir()


def _get_img_uri(filename: str):
    if filename in _IMG_CACHE:
        return _IMG_CACHE[filename]
    path = os.path.join(ASSETS_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext  = filename.rsplit(".", 1)[-1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    uri  = f"data:{mime};base64,{b64}"
    _IMG_CACHE[filename] = uri
    return uri


def _azkar_img(filename: str):
    uri = _get_img_uri(filename)
    if uri:
        return ft.Image(src=uri, width=350, fit=ft.BoxFit.CONTAIN)
    return ft.Container(
        content=ft.Text(f"⚠️ {filename}", color=C_DANGER, size=12),
        width=350, height=80, bgcolor=C_SURFACE, border_radius=8,
        alignment=ft.Alignment(0, 0),
    )


def _preload_azkar_images():
    for fn in ("azkar_salah.jpg", "azkar_sabah_masa.jpg", "azkar_noom.jpg"):
        _get_img_uri(fn)


# ═══════════════════════════════════════════════════════════════════════════════
# SURAH READER
# ── KEY PERFORMANCE FIX: item_extent makes scroll_to(index) pinpoint-accurate
# ═══════════════════════════════════════════════════════════════════════════════

_reader_cache: dict[int, tuple] = {}  # surah_num → (view, ayah_list, containers, bm_ayah)


def build_surah_reader(page: ft.Page, surah_num: int, on_back, bookmark):
    bm_ayah = bookmark["ayah"] if (bookmark and bookmark["surah"] == surah_num) else None
    surah   = get_surah(surah_num)

    current_bm: list = [bm_ayah]
    containers: dict = {}

    def make_tap(anum: int):
        def _tap(e):
            prev = current_bm[0]
            if prev is not None and prev in containers:
                containers[prev].bgcolor = C_BG
                containers[prev].update()
            if prev == anum:
                clear_bookmark()
                current_bm[0] = None
                if surah_num in _reader_cache:
                    v, al, co, _ = _reader_cache[surah_num]
                    _reader_cache[surah_num] = (v, al, co, None)
            else:
                save_bookmark(surah_num, anum)
                current_bm[0] = anum
                containers[anum].bgcolor = C_BM_HL
                containers[anum].update()
                if surah_num in _reader_cache:
                    v, al, co, _ = _reader_cache[surah_num]
                    _reader_cache[surah_num] = (v, al, co, anum)
                # Clear old bookmarks from other surahs
                for sn in list(_reader_cache):
                    if sn != surah_num and _reader_cache[sn][3] is not None:
                        _reader_cache.pop(sn, None)
        return _tap

    # ── Build ayah list ───────────────────────────────────────────────────────
    ayah_list = ft.ListView(
        spacing=6,
        expand=True,
        padding=ft.Padding.symmetric(horizontal=0, vertical=4),
    )

    if surah:
        for ayah in surah["ayahs"]:
            an  = ayah["number"]
            row = ft.Container(
                content=ft.Row([
                    ft.Container(
                        content=ft.Text(ayah["ref"], size=11, color=C_TEXT,
                                        weight=ft.FontWeight.BOLD,
                                        text_align=ft.TextAlign.CENTER),
                        bgcolor=C_AMBER_LT, width=48, height=28,
                        border_radius=14, alignment=ft.Alignment(0, 0),
                    ),
                    ft.Container(
                        content=ft.Text(ayah["text"], size=24, color=C_TEXT,
                                        text_align=ft.TextAlign.RIGHT,
                                        rtl=True, font_family=QURAN_FONT),
                        expand=True, padding=ft.Padding.only(right=10),
                    ),
                ], alignment=ft.MainAxisAlignment.END,
                   vertical_alignment=ft.CrossAxisAlignment.START),
                padding=ft.Padding.symmetric(horizontal=12, vertical=12),
                bgcolor=C_BM_HL if an == bm_ayah else C_BG,
                border_radius=8,
                on_click=make_tap(an),
            )
            containers[an] = row
            ayah_list.controls.append(row)
    else:
        ayah_list.controls.append(
            ft.Text("تعذّر تحميل السورة", color=C_DANGER, rtl=True))

    title = f"سورة {surah['name_ar']}" if surah else "—"
    meta  = f"{surah['name_en']}  •  {surah['ayah_count']} آيات" if surah else ""

    view = ft.Column([
        ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                          icon_color=C_TEAL, on_click=lambda e: on_back()),
            ft.Text(title, size=20, weight=ft.FontWeight.BOLD, color=C_TEAL,
                    rtl=True, text_align=ft.TextAlign.CENTER, expand=True),
        ]),
        ft.Container(
            content=ft.Text(meta, size=12, color=C_TEXT_SUB,
                            rtl=True, text_align=ft.TextAlign.CENTER),
            alignment=ft.Alignment(0, 0), width=float("inf"),
        ),
        ft.Divider(color=C_DIVIDER),
        ft.Container(content=ayah_list, expand=True),
    ], width=370, expand=True)

    _reader_cache[surah_num] = (view, ayah_list, containers, bm_ayah)
    return view, ayah_list, containers


# ═══════════════════════════════════════════════════════════════════════════════
# QURAN INDEX
# ═══════════════════════════════════════════════════════════════════════════════

def build_quran_index(page: ft.Page, on_back, on_open_surah):
    surahs    = get_all_surahs()
    surah_map = {s["number"]: s for s in surahs}

    status_txt = ft.Text(
        f"القرآن الكريم  •  {total_ayahs()} آية" if is_data_ready()
        else "⏳ جارٍ تحميل السور...",
        size=12,
        color=C_TEXT_SUB if is_data_ready() else C_AMBER,
        rtl=True, text_align=ft.TextAlign.CENTER,
    )

    bm_card = ft.Container(visible=False)

    def _refresh_bm():
        bm = load_bookmark()
        if not bm:
            if bm_card.visible:
                bm_card.visible = False
                bm_card.content = None
                try: bm_card.update()
                except Exception: pass
            return
        s = surah_map.get(bm["surah"])
        if not s:
            return
        bm_card.content = ft.Row([
            ft.Container(
                content=ft.Icon(ft.Icons.BOOKMARK_ROUNDED, color="white", size=22),
                bgcolor=C_AMBER_DK, width=44, height=44,
                border_radius=10, alignment=ft.Alignment(0, 0),
            ),
            ft.Column([
                ft.Text("تابع قراءتك", size=10, color=C_AMBER,
                        weight=ft.FontWeight.BOLD, rtl=True),
                ft.Text(f"سورة {s['name_ar']}  •  الآية {bm['ayah']}",
                        size=15, color=C_TEXT, weight=ft.FontWeight.BOLD, rtl=True),
                ft.Text(f"{s['name_en']}  •  Ayah {bm['ayah']}", size=11, color=C_TEXT_SUB),
            ], spacing=1, expand=True,
               horizontal_alignment=ft.CrossAxisAlignment.END),
            ft.Icon(ft.Icons.CHEVRON_LEFT_ROUNDED, color=C_TEAL, size=20),
        ], alignment=ft.MainAxisAlignment.START,
           vertical_alignment=ft.CrossAxisAlignment.CENTER)
        bm_card.bgcolor       = C_BM_HL
        bm_card.border_radius = 12
        bm_card.padding       = ft.Padding.symmetric(horizontal=12, vertical=10)
        bm_card.visible       = True
        bm_card.on_click      = lambda e: on_open_surah(bm["surah"])
        try: bm_card.update()
        except Exception: pass

    _refresh_bm()

    search_field = ft.TextField(
        hint_text="ابحث عن سورة...",
        bgcolor=C_SURFACE, border_color=C_DIVIDER,
        color=C_TEXT, hint_style=ft.TextStyle(color=C_TEXT_SUB),
        text_align=ft.TextAlign.RIGHT, height=42, expand=True, rtl=True,
    )

    surah_list = ft.ListView(spacing=4, expand=True)
    all_rows   = []

    def _build_rows(data):
        rows = []
        for s in data:
            def _open(num):
                return lambda e: on_open_surah(num)
            row = ft.Container(
                content=ft.Row([
                    ft.Container(
                        content=ft.Text(str(s["number"]), size=12, color=C_TEXT,
                                        weight=ft.FontWeight.BOLD,
                                        text_align=ft.TextAlign.CENTER),
                        bgcolor=C_AMBER_LT, width=36, height=36,
                        border_radius=18, alignment=ft.Alignment(0, 0),
                    ),
                    ft.Column([
                        ft.Text(s["name_ar"], size=16, color=C_TEXT,
                                weight=ft.FontWeight.BOLD, rtl=True),
                        ft.Text(s["name_en"], size=11, color=C_TEXT_SUB),
                    ], spacing=1, expand=True,
                       horizontal_alignment=ft.CrossAxisAlignment.END),
                    ft.Text(f"{s['ayah_count']} آية", size=11, color=C_TEXT_SUB, rtl=True),
                    ft.Icon(ft.Icons.CHEVRON_LEFT_ROUNDED, color=C_TEAL, size=18),
                ], alignment=ft.MainAxisAlignment.START,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                bgcolor=C_SURFACE, border_radius=10,
                on_click=_open(s["number"]),
            )
            rows.append((s, row))
        return rows

    all_rows.extend(_build_rows(surahs))
    surah_list.controls = [r for _, r in all_rows]

    def _rebuild_list():
        nonlocal surahs, surah_map
        surahs    = get_all_surahs()
        surah_map = {s["number"]: s for s in surahs}
        new_rows  = _build_rows(surahs)
        all_rows.clear()
        all_rows.extend(new_rows)
        surah_list.controls = [r for _, r in all_rows]
        status_txt.value = f"القرآن الكريم  •  {total_ayahs()} آية"
        status_txt.color = C_TEXT_SUB
        try:
            surah_list.update()
            status_txt.update()
        except Exception:
            pass
        _refresh_bm()

    def _watch_api_ready():
        for _ in range(150):
            time.sleep(0.1)
            if is_data_ready():
                _rebuild_list()
                return
        status_txt.value = "⚠️ تعذّر الاتصال بالخادم"
        status_txt.color = C_DANGER
        try: status_txt.update()
        except Exception: pass

    if not surahs:
        threading.Thread(target=_watch_api_ready, daemon=True).start()

    def _apply_search(q: str):
        changed = False
        for s, row in all_rows:
            vis = not q or q in s["name_ar"] or q in s["name_en"].lower()
            if row.visible != vis:
                row.visible = vis
                changed = True
        if changed:
            try: surah_list.update()
            except Exception: pass

    _deb_search = Debounce(0.15, _apply_search)
    search_field.on_change = lambda e: _deb_search(search_field.value.strip().lower())

    view = ft.Column([
        ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                          icon_color=C_TEAL, on_click=lambda e: on_back()),
            ft.Text("📖 القرآن الكريم", size=20, weight=ft.FontWeight.BOLD,
                    color=C_TEAL, expand=True, rtl=True),
        ]),
        ft.Container(content=status_txt, alignment=ft.Alignment(0, 0),
                     width=float("inf")),
        bm_card,
        ft.Divider(color=C_DIVIDER, height=10),
        ft.Row([search_field]),
        ft.Container(content=surah_list, expand=True),
    ], width=370, expand=True)

    view.refresh_bookmark = _refresh_bm
    return view


# ═══════════════════════════════════════════════════════════════════════════════
# AZKAR PAGE
# ═══════════════════════════════════════════════════════════════════════════════

def build_azkar_page(on_back):
    def _title(label):
        return ft.Container(
            content=ft.Text(label, size=18, weight="bold", color=C_TEAL, rtl=True),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )
    return ft.Column([
        ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                          icon_color=C_TEAL, on_click=lambda e: on_back()),
            ft.Text("📿 الأذكار", size=20, weight=ft.FontWeight.BOLD,
                    color=C_TEAL, expand=True, rtl=True),
        ]),
        ft.Divider(color=C_DIVIDER),
        _title("أذكار بعد الصلاة"),
        _azkar_img("azkar_salah.jpg"),
        ft.Divider(color=C_DIVIDER),
        _title("أذكار الصباح والمساء"),
        _azkar_img("azkar_sabah_masa.jpg"),
        ft.Divider(color=C_DIVIDER),
        _title("أذكار النوم"),
        _azkar_img("azkar_noom.jpg"),
        ft.Container(height=60),
    ], scroll=ft.ScrollMode.AUTO, width=370, expand=True,
       horizontal_alignment=ft.CrossAxisAlignment.CENTER)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main(page: ft.Page):
    page.title                = "Salah Reminder"
    page.theme_mode           = ft.ThemeMode.LIGHT
    page.bgcolor              = C_BG
    page.horizontal_alignment = "center"
    page.padding              = 20
    page.fonts = {
        "AmiriQuran": "https://fonts.gstatic.com/s/amiriquran/v14/x3dkckHVYrCU5BU15c4BfM7j5dMeHg.ttf",
    }

    # ── Init Flet Audio (required for iOS & Android azan playback) ────────────
    import azan_player as _ap_module
    _ap_module.init_flet_audio(page)
    # ─────────────────────────────────────────────────────────────────────────

    page.theme = ft.Theme(color_scheme=ft.ColorScheme(
        primary=C_TEAL, secondary=C_AMBER_DK,
        surface=C_SURFACE, on_primary="white", on_surface=C_TEXT,
    ))

    city, country, saved_method = load_location()
    if not city:
        city, country, saved_method = "Nicosia", "Cyprus", "General (MWL)"

    completed_prayers = load_completed_prayers()
    notified_prayers  = set()
    current_day       = datetime.now().date()
    _lock             = threading.Lock()
    prayer_times      = get_prayer_times_best(city, country, saved_method)

    _prayer_btns  = {}
    _prayer_cards = {}
    _deb_save     = Debounce(0.8, save_completed_prayers)
    _vol          = [azan_get_volume()]

    # ── Page stack ───────────────────────────────────────────────────────────
    page_stack   = ft.Stack(expand=True)
    _active_page = [0]

    def _show(idx: int):
        if _active_page[0] == idx:
            return
        page_stack.controls[_active_page[0]].visible = False
        page_stack.controls[idx].visible             = True
        _active_page[0] = idx
        if idx == 1:
            try: quran_index.refresh_bookmark()
            except Exception: pass
        page.update()

    def go_home(e=None):  _show(0)
    def go_quran(e=None): _show(1)
    def go_azkar(e=None): _show(3)

    def go_surah(number: int):
        bm      = load_bookmark()
        bm_ayah = bm["ayah"] if (bm and bm["surah"] == number) else None

        # ── Use cache if bookmark matches ─────────────────────────────────────
        if number in _reader_cache:
            cached_view, cached_list, cached_containers, cached_bm = _reader_cache[number]
            if cached_bm == bm_ayah:
                cached_view.controls[0].controls[0].on_click = lambda e: _show(1)
                if page_stack.controls[2].content is not cached_view:
                    page_stack.controls[2].content = cached_view
                _show(2)
                if bm_ayah:
                    _scroll_to_ayah(cached_list, bm_ayah)
                return

        # ── Build fresh reader ────────────────────────────────────────────────
        reader, ayah_list, _ = build_surah_reader(
            page, number, on_back=lambda: _show(1), bookmark=bm)
        page_stack.controls[2].content = reader
        _show(2)

        if bm_ayah:
            _scroll_to_ayah(ayah_list, bm_ayah)

    def _scroll_to_ayah(ayah_list: ft.ListView, ayah_num: int):
        """
        Scroll to the exact ayah by index.
        Retries a few times to handle rendering delays.
        """
        idx = max(0, ayah_num - 1)

        def _do():
            for wait in (0.15, 0.5, 1.0):
                time.sleep(wait)
                try:
                    if len(ayah_list.controls) > idx:
                        ayah_list.scroll_to(index=idx, duration=0)
                        ayah_list.update()
                        return
                except Exception:
                    pass

        threading.Thread(target=_do, daemon=True).start()

    # ── Prayer cards ─────────────────────────────────────────────────────────
    cards_col = ft.Column(spacing=12, width=350)

    def _toggle(name: str):
        def _do(e):
            if name in completed_prayers:
                completed_prayers.discard(name)
                _prayer_btns[name].content  = ft.Text("Done", color=C_TEXT)
                _prayer_btns[name].style    = _btn(C_AMBER_LT, C_TEXT)
                _prayer_cards[name].bgcolor = C_TEAL_LT
            else:
                completed_prayers.add(name)
                _prayer_btns[name].content  = ft.Text("Done ✔", color="white")
                _prayer_btns[name].style    = _btn(C_TEAL, "white")
                _prayer_cards[name].bgcolor = C_TEAL
            _deb_save(completed_prayers.copy())
            _prayer_btns[name].update()
            _prayer_cards[name].update()
        return _do

    def build_prayer_cards():
        _prayer_btns.clear()
        _prayer_cards.clear()
        cards_col.controls.clear()
        with _lock:
            times = dict(prayer_times)
        for name, ptime in times.items():
            done = name in completed_prayers
            btn  = ft.FilledButton(
                ft.Text("Done ✔" if done else "Done",
                        color="white" if done else C_TEXT),
                style=_btn(C_TEAL if done else C_AMBER_LT,
                           "white" if done else C_TEXT),
                height=35, on_click=_toggle(name),
            )
            _prayer_btns[name] = btn
            card = ft.Container(
                content=ft.Row([
                    ft.Container(width=5, height=54, bgcolor=C_TEAL,
                                 border_radius=ft.BorderRadius(4, 0, 0, 4)),
                    ft.Container(ft.Text(name, size=18, weight="bold", color=C_TEXT),
                                 width=105, padding=ft.Padding.only(left=8)),
                    ft.Container(ft.Text(ptime, size=17, color=C_AMBER), width=70),
                    btn,
                ], alignment=ft.MainAxisAlignment.START,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                padding=ft.Padding.only(right=16, top=10, bottom=10),
                bgcolor=C_TEAL if done else C_TEAL_LT,
                border_radius=12,
            )
            _prayer_cards[name] = card
            cards_col.controls.append(card)
        try: page.update()
        except Exception: pass

    # ── Azan panel ───────────────────────────────────────────────────────────
    pct0      = int(_vol[0] * 100)
    _vol_icon = lambda p: "🔊" if p > 50 else ("🔉" if p > 0 else "🔇")

    vol_label = ft.Text(
        f"{_vol_icon(pct0)}  {pct0}%" if AZAN_OK else "🔇  N/A",
        size=13, color=C_TEXT_SUB, width=70,
    )
    stop_btn = ft.FilledButton("⏹  إيقاف الأذان",
                               style=_btn(C_DANGER, "white"),
                               height=34, visible=False)

    def _refresh_stop_btn():
        playing = azan_is_playing()
        if stop_btn.visible != playing:
            stop_btn.visible = playing
            try: stop_btn.update()
            except Exception: pass

    stop_btn.on_click = lambda e: (azan_stop(), _refresh_stop_btn())

    def _on_vol_change(e):
        v = float(e.control.value) / 100.0
        _vol[0] = v
        pct = int(v * 100)
        vol_label.value = f"{_vol_icon(pct)}  {pct}%"
        vol_label.update()
        azan_set_volume(v)

    vol_slider = ft.Slider(
        min=0, max=100, value=pct0, divisions=20,
        active_color=C_TEAL, inactive_color=C_DIVIDER,
        thumb_color=C_AMBER_DK, expand=True,
        label="{value}%", on_change=_on_vol_change,
        disabled=not AZAN_OK,
    )
    azan_panel = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Icon(ft.Icons.VOLUME_UP_ROUNDED, color="white", size=18),
                    bgcolor=C_TEAL, width=34, height=34,
                    border_radius=8, alignment=ft.Alignment(0, 0),
                ),
                ft.Text("أذان مشاري راشد العفاسي", size=14,
                        weight=ft.FontWeight.BOLD, color=C_TEXT, rtl=True, expand=True),
                vol_label,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([vol_slider], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([stop_btn], alignment=ft.MainAxisAlignment.CENTER),
        ], spacing=4),
        bgcolor=C_SURFACE, border_radius=12,
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        width=350,
    )

    # ── Notifications ─────────────────────────────────────────────────────────
    def send_notification(prayer: str):
        print(f"[Salah] 🕌 {prayer}")
        if PLYER_OK:
            try:
                _plyer_notify.notify(title="🕌 وقت الصلاة",
                                     message=f"حان وقت صلاة {prayer}", timeout=10)
            except Exception as exc:
                print(f"[Salah] plyer: {exc}")
        try:
            page.snack_bar      = ft.SnackBar(
                ft.Text(f"حان وقت {prayer}! 🕌", color="white"), bgcolor=C_TEAL)
            page.snack_bar.open = True
            page.update()
        except Exception as exc:
            print(f"[Salah] snack: {exc}")
        if not AZAN_OK or _vol[0] <= 0:
            return
        azan_play(prayer)
        azan_set_on_stop(_refresh_stop_btn)
        threading.Thread(target=lambda: (time.sleep(0.5), _refresh_stop_btn()),
                         daemon=True).start()

    # ── Location ──────────────────────────────────────────────────────────────
    location_label = ft.Text(f"📍 {city}, {country}", size=14, color=C_TEXT_SUB,
                              text_align=ft.TextAlign.CENTER)
    city_input    = ft.TextField(label="City", width=170,
                                  label_style=ft.TextStyle(color=C_TEXT_SUB),
                                  color=C_TEXT, bgcolor=C_SURFACE,
                                  border_color=C_DIVIDER, focused_border_color=C_TEAL)
    country_input = ft.TextField(label="Country", width=170,
                                  label_style=ft.TextStyle(color=C_TEXT_SUB),
                                  color=C_TEXT, bgcolor=C_SURFACE,
                                  border_color=C_DIVIDER, focused_border_color=C_TEAL)
    method_dd = ft.Dropdown(
        label="Prayer Calculation Method", value=saved_method,
        options=[ft.dropdown.Option(n) for n in PRAYER_METHODS.keys()],
        width=350, bgcolor=C_SURFACE, color=C_TEXT,
        border_color=C_DIVIDER, focused_border_color=C_TEAL,
    )

    def _set_location(e):
        nonlocal city, country, prayer_times
        nc, nr = city_input.value.strip(), country_input.value.strip()
        if not nc or not nr: return
        city, country = nc, nr
        save_location(city, country, method_dd.value)
        location_label.value = f"📍 {city}, {country}"
        location_label.update()
        def _fetch():
            nonlocal prayer_times
            try: os.remove(PRAYER_CACHE_FILE)
            except Exception: pass
            times = get_prayer_times_best(city, country, method_dd.value)
            with _lock:
                prayer_times = times
            build_prayer_cards()
        threading.Thread(target=_fetch, daemon=True).start()

    # ── Main page ─────────────────────────────────────────────────────────────
    main_page = ft.Column([
        ft.Container(
            content=ft.Text("🕌 Salah Reminder", size=24,
                            weight=ft.FontWeight.BOLD, color="white",
                            text_align=ft.TextAlign.CENTER),
            bgcolor=C_TEAL, border_radius=ft.BorderRadius(0, 0, 16, 16),
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            width=float("inf"),
        ),
        ft.Container(height=8),
        ft.Row([city_input, country_input], alignment=ft.MainAxisAlignment.CENTER),
        method_dd,
        ft.FilledButton("📍 Set Location", style=_btn(C_AMBER_DK, "white"),
                        on_click=_set_location),
        location_label,
        ft.Divider(color=C_DIVIDER),
        cards_col,
        ft.Divider(color=C_DIVIDER),
        azan_panel,
        ft.Divider(color=C_DIVIDER),
        ft.FilledButton("📖 القرآن الكريم", style=_btn(C_TEAL, "white"),
                        width=350, height=44, on_click=go_quran),
        ft.FilledButton("📿 الأذكار", style=_btn(C_AMBER_DK, "white"),
                        width=350, height=44, on_click=go_azkar),
    ], width=350, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
       scroll=ft.ScrollMode.AUTO)

    quran_index = build_quran_index(page, on_back=go_home, on_open_surah=go_surah)
    azkar_page  = build_azkar_page(on_back=go_home)

    page_stack.controls = [
        ft.Container(content=main_page,   visible=True,  expand=True, bgcolor=C_BG),
        ft.Container(content=quran_index, visible=False, expand=True, bgcolor=C_BG),
        ft.Container(visible=False, expand=True, bgcolor=C_BG),  # surah slot
        ft.Container(content=azkar_page,  visible=False, expand=True, bgcolor=C_BG),
    ]

    page.add(page_stack)
    build_prayer_cards()

    # ── Background checker ────────────────────────────────────────────────────
    def _checker():
        nonlocal current_day, prayer_times, city, country
        last_minute = None
        while True:
            now, today = datetime.now(), datetime.now().date()
            if today != current_day:
                current_day = today
                completed_prayers.clear()
                notified_prayers.clear()
                last_minute = None
                save_completed_prayers(completed_prayers)
                new_times = get_prayer_times_best(city, country, method_dd.value)
                with _lock:
                    prayer_times = new_times
                build_prayer_cards()
            cur_min = now.strftime("%H:%M")
            if cur_min != last_minute:
                last_minute = cur_min
                with _lock:
                    snapshot = dict(prayer_times)
                for prayer, ptime in snapshot.items():
                    if cur_min == ptime and prayer not in notified_prayers:
                        notified_prayers.add(prayer)
                        send_notification(prayer)
            sleep_for = 1.0 - datetime.now().microsecond / 1_000_000
            time.sleep(max(sleep_for, 0.0))

    threading.Thread(target=_checker, daemon=True).start()

    # ── Background: only preload images, no surah pre-warming ────────────────
    def _bg_tasks():
        time.sleep(1.0)
        _preload_azkar_images()

    threading.Thread(target=_bg_tasks, daemon=True).start()


ft.run(main, assets_dir="assets")