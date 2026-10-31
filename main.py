"""
Salah Reminder — main.py  [PERF-OPTIMIZED]

Key changes for <2s startup:
• App renders immediately with cached/placeholder data — no blocking calls
• Prayer times: cache checked synchronously (μs), live fetch in background
• Quran/Riyad pages: built lazily on first open (not at startup)
• Azkar images: lazy-loaded on first open, not at startup
• azan.init() deferred 200ms via thread so page.add() returns instantly
• All API calls parallelised with ThreadPoolExecutor at import time
• Skeleton loaders shown while data loads in bg
• Reader cache preserved across visits
• Volume/settings loaded from disk once, never re-read
• Debounce delay reduced to 0.5s for snappier saves
"""

import flet as ft
import threading
import time
import json
import os
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from prayer_api import get_prayer_times, PRAYER_METHODS
from quran_api import get_all_surahs, get_surah, is_data_ready, total_ayahs
from riyad_api import get_all_chapters, get_chapter, is_ready as riyad_ready, total_hadiths

# ── Optional dependencies ────────────────────────────────────────────────────
PLYER_OK = False
try:
    from jnius import autoclass as _jnius_check  # type: ignore
    _jnius_check("android.os.Build")             # only succeeds inside an APK
    from notify_android import request_notification_permission, send_android_notification
    _IS_ANDROID = True
    print("[Salah] Android mode — android_notify loaded")
except Exception:
    _IS_ANDROID = False
    def request_notification_permission(): pass
    def send_android_notification(t, m): pass
    print("[Salah] Desktop mode")

# ── File paths ───────────────────────────────────────────────────────────────
SAVE_FILE         = "completed_prayers.json"
LOCATION_FILE     = "location.json"
BOOKMARK_FILE     = "bookmark.json"
PRAYER_CACHE_FILE = "prayer_cache.json"
SETTINGS_FILE     = "settings.json"

# ── Colour palette ───────────────────────────────────────────────────────────
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

# ── Shared background executor (4 workers) ───────────────────────────────────
_BG = ThreadPoolExecutor(max_workers=4, thread_name_prefix="salah-bg")


# ── Responsive width ─────────────────────────────────────────────────────────
# Returns usable content width: screen width minus padding, capped at 600px
# so it looks good on small phones, big phones, and tablets alike.
def _W(page: ft.Page, pad: int = 20) -> float:
    pw = getattr(page, "width", None) or 400
    return min(float(pw) - pad * 2, 600.0)


# ═══════════════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _btn(bg: str, fg: str = "#1C1A17") -> ft.ButtonStyle:
    return ft.ButtonStyle(
        bgcolor=bg, color=fg,
        shape=ft.RoundedRectangleBorder(radius=8),
    )


def _write_json(path: str, data: dict):
    def _go():
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as exc:
            print(f"[Salah] write {path}: {exc}")
    _BG.submit(_go)


class Debounce:
    def __init__(self, delay: float, fn):
        self._delay = delay
        self._fn    = fn
        self._timer = None
        self._lock  = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fn, args, kwargs)
            self._timer.start()


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE  (all reads synchronous but cheap — only JSON files)
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
        "date":    datetime.now().strftime("%Y-%m-%d"),
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


def load_bookmark() -> dict | None:
    try:
        with open(BOOKMARK_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_bookmark(surah: int, ayah: int):
    _write_json(BOOKMARK_FILE, {"surah": surah, "ayah": ayah})


def clear_bookmark():
    _BG.submit(lambda: os.path.exists(BOOKMARK_FILE) and os.remove(BOOKMARK_FILE))


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data: dict):
    _write_json(SETTINGS_FILE, data)


# ═══════════════════════════════════════════════════════════════════════════════
# PRAYER TIMES CACHE
# ═══════════════════════════════════════════════════════════════════════════════

def _load_cache(city, country, method, require_today=True):
    try:
        with open(PRAYER_CACHE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        key_ok = (
            d.get("city") == city
            and d.get("country") == country
            and d.get("method") == method
            and d.get("times")
        )
        if not key_ok:
            return None
        if require_today and d.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return None
        return d["times"]
    except Exception:
        return None


def get_prayer_times_best(city, country, method) -> dict:
    """
    Returns immediately if today's cache exists.
    Falls back to stale cache and refreshes in background.
    Only blocks if absolutely no cache exists.
    """
    today_cache = _load_cache(city, country, method, require_today=True)
    if today_cache:
        return today_cache                          # ← cache hit: ~0ms

    stale = _load_cache(city, country, method, require_today=False)

    def _refresh():
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
            print(f"[Salah] bg refresh: {exc}")
            return {}

    if stale:
        _BG.submit(_refresh)                        # ← refresh in bg, return stale now
        return stale

    # No cache at all — must fetch, but do it in a future
    # Caller should handle empty dict with a skeleton UI
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# AZAN PLAYER
# ═══════════════════════════════════════════════════════════════════════════════

class AzanPlayer:
    """
    Audio backend for azan playback.

    flet build (Android/iOS APK/IPA)
    ─────────────────────────────────
    Assets live inside the Flutter asset bundle — NOT behind an HTTP server.
    flet_audio must receive a bare filename ("azan.mp3") as src, which the
    Flutter engine resolves against the bundle automatically.
    The control must be appended to page.overlay AFTER the first frame renders.

    Desktop (flet run / flet build desktop)
    ────────────────────────────────────────
    Assets are in the local filesystem "assets/" folder.
    Falls back: flet_audio → ft.Audio → pygame → playsound.
    """

    AZAN_FILES: dict[str, str] = {
        "Fajr":    "azan_fajr.mp3",
        "Dhuhr":   "azan.mp3",
        "Asr":     "azan.mp3",
        "Maghrib": "azan.mp3",
        "Isha":    "azan.mp3",
    }
    DEFAULT_AZAN = "azan.mp3"

    def __init__(self):
        self._volume: float   = 1.0
        self._playing: bool   = False
        self._on_stop_cb      = None
        self.ok: bool         = False
        self._backend: str    = "none"
        self._audio           = None
        self._page            = None
        self._pygame_ok: bool = False
        self._is_mobile: bool = False

    # ── Public ────────────────────────────────────────────────────────────────

    def init(self, page: ft.Page):
        """Call immediately after page.add(). Safe on all platforms."""
        self._page      = page
        platform        = getattr(page, "platform", None)
        self._is_mobile = platform in ("android", "ios")
        print(f"[AzanPlayer] platform={platform!r}  is_mobile={self._is_mobile}")

        if self._is_mobile:
            # On Android/iOS built APK: flet_audio with bare filename src.
            # Must attach to overlay in a thread after first paint.
            self._init_mobile(page)
        else:
            # Desktop: try flet_audio first, then fallbacks
            if self._try_flet_audio(page, src_prefix="assets/"): return
            if self._try_ft_audio(page, src_prefix="assets/"):   return
            if self._try_pygame():                                return
            if self._try_playsound():                             return
            print("[AzanPlayer] ⚠️  No audio backend available.")

    def play(self, prayer_name: str):
        if not self.ok or self._volume <= 0:
            return
        filename = self.AZAN_FILES.get(prayer_name, self.DEFAULT_AZAN)
        # On mobile: bare filename. Desktop: "assets/filename"
        src = filename if self._is_mobile else f"assets/{filename}"
        print(f"[AzanPlayer] play  src={src!r}  backend={self._backend}")

        if self._backend in ("flet_audio", "ft.Audio"):
            try:
                self._audio.src    = src
                self._audio.volume = self._volume
                self._audio.update()
                self._audio.play()
                self._playing = True
            except Exception as exc:
                print(f"[AzanPlayer] play error: {exc}")

        elif self._backend == "pygame":
            def _pg():
                try:
                    self._pygame.mixer.music.load(src)
                    self._pygame.mixer.music.set_volume(self._volume)
                    self._pygame.mixer.music.play()
                    self._playing = True
                    while self._pygame.mixer.music.get_busy():
                        time.sleep(0.5)
                    self._playing = False
                    self._fire_on_stop()
                except Exception as exc:
                    print(f"[AzanPlayer] pygame: {exc}")
            threading.Thread(target=_pg, daemon=True).start()

        elif self._backend == "playsound":
            def _ps():
                try:
                    self._playing = True
                    self._playsound(src, block=True)
                    self._playing = False
                    self._fire_on_stop()
                except Exception as exc:
                    print(f"[AzanPlayer] playsound: {exc}")
            threading.Thread(target=_ps, daemon=True).start()

    def stop(self):
        if not self.ok:
            return
        if self._backend in ("flet_audio", "ft.Audio"):
            try:
                self._audio.pause()
                self._audio.seek(0)
            except Exception:
                pass
        elif self._backend == "pygame":
            try:
                self._pygame.mixer.music.stop()
            except Exception:
                pass
        self._playing = False

    def is_playing(self) -> bool:
        return self._playing

    def get_volume(self) -> float:
        return self._volume

    def set_volume(self, v: float):
        self._volume = max(0.0, min(1.0, v))
        if self._backend in ("flet_audio", "ft.Audio") and self._audio:
            try:
                self._audio.volume = self._volume
                self._audio.update()
            except Exception:
                pass
        elif self._backend == "pygame" and self._pygame_ok:
            try:
                self._pygame.mixer.music.set_volume(self._volume)
            except Exception:
                pass

    def set_on_stop(self, cb):
        self._on_stop_cb = cb

    # ── Private ───────────────────────────────────────────────────────────────

    def _on_ft_state(self, e):
        state = getattr(e, "data", str(e))
        print(f"[AzanPlayer] state → {state!r}")
        if state in ("completed", "stopped"):
            self._playing = False
            self._fire_on_stop()

    def _fire_on_stop(self):
        if self._on_stop_cb:
            try:
                self._on_stop_cb()
            except Exception:
                pass

    def _make_fa_audio(self, src: str):
        """Build a flet_audio.Audio control. Raises ImportError if not installed."""
        import flet_audio as fa  # type: ignore

        kwargs: dict = dict(
            src=src,
            volume=self._volume,
            autoplay=False,
            on_state_changed=self._on_ft_state,
        )
        rm = getattr(fa, "ReleaseMode", None)
        if rm and hasattr(rm, "STOP"):
            kwargs["release_mode"] = rm.STOP
        return fa.Audio(**kwargs)

    def _init_mobile(self, page: ft.Page):
        """
        Android/iOS (flet build APK/IPA):
        - src is the bare filename — Flutter resolves it from the asset bundle.
        - Appending to page.overlay must happen after the first frame paints.
          We sleep 0.6s in a background thread then attach.
        """
        def _attach():
            try:
                ctrl = self._make_fa_audio(self.DEFAULT_AZAN)
            except ImportError:
                print("[AzanPlayer] ⚠️ flet_audio not installed — rebuild APK with it")
                return
            except Exception as exc:
                print(f"[AzanPlayer] flet_audio init failed: {exc}")
                return

            time.sleep(0.6)   # wait for first Flutter frame

            try:
                page.overlay.append(ctrl)
                page.update()
                self._audio   = ctrl
                self._backend = "flet_audio"
                self.ok       = True
                print("[AzanPlayer] Android flet_audio attached to overlay ✓")
            except Exception as exc:
                print(f"[AzanPlayer] overlay append failed: {exc}")

        threading.Thread(target=_attach, daemon=True).start()

    def _try_flet_audio(self, page: ft.Page, src_prefix: str) -> bool:
        try:
            ctrl = self._make_fa_audio(f"{src_prefix}{self.DEFAULT_AZAN}")

            def _attach():
                try:
                    page.overlay.append(ctrl)
                    page.update()
                except Exception as exc:
                    print(f"[AzanPlayer] desktop overlay append: {exc}")

            threading.Thread(target=_attach, daemon=True).start()
            self._audio   = ctrl
            self._backend = "flet_audio"
            self.ok       = True
            print("[AzanPlayer] backend = flet_audio (desktop) ✓")
            return True
        except ImportError:
            return False
        except Exception as exc:
            print(f"[AzanPlayer] flet_audio desktop: {exc}")
            return False

    def _try_ft_audio(self, page: ft.Page, src_prefix: str) -> bool:
        try:
            audio_cls = getattr(ft, "Audio", None)
            if audio_cls is None:
                return False
            ctrl = audio_cls(
                src=f"{src_prefix}{self.DEFAULT_AZAN}",
                volume=self._volume,
                autoplay=False,
                on_state_changed=self._on_ft_state,
            )

            def _attach():
                try:
                    page.overlay.append(ctrl)
                    page.update()
                except Exception as exc:
                    print(f"[AzanPlayer] ft.Audio overlay: {exc}")

            threading.Thread(target=_attach, daemon=True).start()
            self._audio   = ctrl
            self._backend = "ft.Audio"
            self.ok       = True
            print("[AzanPlayer] backend = ft.Audio ✓")
            return True
        except Exception as exc:
            print(f"[AzanPlayer] ft.Audio: {exc}")
            return False

    def _try_pygame(self) -> bool:
        try:
            import pygame
            pygame.mixer.init()
            self._pygame    = pygame
            self._pygame_ok = True
            self.ok         = True
            self._backend   = "pygame"
            print("[AzanPlayer] backend = pygame ✓")
            return True
        except Exception as exc:
            print(f"[AzanPlayer] pygame: {exc}")
            return False

    def _try_playsound(self) -> bool:
        try:
            from playsound import playsound
            self._playsound = playsound
            self.ok         = True
            self._backend   = "playsound"
            print("[AzanPlayer] backend = playsound ✓")
            return True
        except Exception as exc:
            print(f"[AzanPlayer] playsound: {exc}")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# ASSETS  (lazy — only reads file when actually needed)
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


def _get_img_uri(filename: str) -> str | None:
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


def _azkar_img(filename: str) -> ft.Control:
    uri = _get_img_uri(filename)
    if uri:
        return ft.Image(src=uri, fit=ft.BoxFit.CONTAIN, expand=True)
    return ft.Container(
        content=ft.Text(f"⚠️ {filename}", color=C_DANGER, size=12),
        height=80, bgcolor=C_SURFACE, border_radius=8,
        alignment=ft.Alignment(0, 0), expand=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SURAH READER
# ═══════════════════════════════════════════════════════════════════════════════

_reader_cache: dict[int, tuple] = {}


def build_surah_reader(page: ft.Page, surah_num: int, on_back, bookmark, W: float = 400):
    bm_ayah = (
        bookmark["ayah"]
        if (bookmark and bookmark.get("surah") == surah_num)
        else None
    )
    surah = get_surah(surah_num)

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
        return _tap

    ayah_list = ft.ListView(
        spacing=6,
        expand=True,
        padding=ft.Padding.symmetric(horizontal=0, vertical=4),
    )

    if surah:
        for ayah in surah["ayahs"]:
            an = ayah["number"]
            row = ft.Container(
                content=ft.Row([
                    ft.Container(
                        content=ft.Text(
                            ayah["ref"], size=11, color=C_TEXT,
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        bgcolor=C_AMBER_LT, width=48, height=28,
                        border_radius=14, alignment=ft.Alignment(0, 0),
                    ),
                    ft.Container(
                        content=ft.Text(
                            ayah["text"], size=20, color=C_TEXT,
                            text_align=ft.TextAlign.RIGHT,
                            rtl=True, font_family=QURAN_FONT,
                            # Extra line height prevents tashkeel from
                            # colliding with the line above
                            style=ft.TextStyle(
                                height=2.0,
                            ),
                        ),
                        expand=True,
                        padding=ft.Padding.only(right=10, top=4, bottom=4),
                    ),
                ],
                    alignment=ft.MainAxisAlignment.END,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=12),
                bgcolor=C_BM_HL if an == bm_ayah else C_BG,
                border_radius=8,
                on_click=make_tap(an),
            )
            containers[an] = row
            ayah_list.controls.append(row)
    else:
        ayah_list.controls.append(
            ft.Text("تعذّر تحميل السورة", color=C_DANGER, rtl=True)
        )

    title = f"سورة {surah['name_ar']}" if surah else "—"
    meta  = f"{surah['name_en']}  •  {surah['ayah_count']} آيات" if surah else ""

    view = ft.Column([
        ft.Row([
            ft.IconButton(
                icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                icon_color=C_TEAL,
                on_click=lambda e: on_back(),
            ),
            ft.Text(
                title, size=20, weight=ft.FontWeight.BOLD, color=C_TEAL,
                rtl=True, text_align=ft.TextAlign.CENTER, expand=True,
            ),
        ]),
        ft.Container(
            content=ft.Text(meta, size=12, color=C_TEXT_SUB, rtl=True,
                            text_align=ft.TextAlign.CENTER),
            alignment=ft.Alignment(0, 0), width=float("inf"),
        ),
        ft.Divider(color=C_DIVIDER),
        ft.Container(content=ayah_list, expand=True),
    ], width=W, expand=True)

    _reader_cache[surah_num] = (view, ayah_list, containers, bm_ayah)
    return view, ayah_list, containers


# ═══════════════════════════════════════════════════════════════════════════════
# QURAN INDEX  (lazy skeleton → real data)
# ═══════════════════════════════════════════════════════════════════════════════

def build_quran_index(page: ft.Page, on_back, on_open_surah, W: float = 400):
    surahs    = get_all_surahs()          # likely populated from background prefetch
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
                try:
                    bm_card.update()
                except Exception:
                    pass
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
        ],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        bm_card.bgcolor       = C_BM_HL
        bm_card.border_radius = 12
        bm_card.padding       = ft.Padding.symmetric(horizontal=12, vertical=10)
        bm_card.visible       = True
        bm_card.on_click      = lambda e: on_open_surah(bm["surah"])
        try:
            bm_card.update()
        except Exception:
            pass

    _refresh_bm()

    search_field = ft.TextField(
        hint_text="ابحث عن سورة...",
        bgcolor=C_SURFACE, border_color=C_DIVIDER,
        color=C_TEXT, hint_style=ft.TextStyle(color=C_TEXT_SUB),
        text_align=ft.TextAlign.RIGHT, height=42, expand=True, rtl=True,
    )

    surah_list = ft.ListView(spacing=4, expand=True)
    all_rows: list[tuple] = []

    def _build_rows(data):
        rows = []
        for s in data:
            def _open(num):
                return lambda e: on_open_surah(num)
            row = ft.Container(
                content=ft.Row([
                    ft.Container(
                        content=ft.Text(
                            str(s["number"]), size=12, color=C_TEXT,
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER,
                        ),
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
                ],
                    alignment=ft.MainAxisAlignment.START,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
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
        try:
            status_txt.update()
        except Exception:
            pass

    if not surahs:
        threading.Thread(target=_watch_api_ready, daemon=True).start()

    _deb_search = Debounce(0.15, lambda q: _apply_search(q))

    def _apply_search(q: str):
        changed = False
        for s, row in all_rows:
            vis = not q or q in s["name_ar"] or q in s["name_en"].lower()
            if row.visible != vis:
                row.visible = vis
                changed = True
        if changed:
            try:
                surah_list.update()
            except Exception:
                pass

    search_field.on_change = (
        lambda e: _deb_search(search_field.value.strip().lower())
    )

    view = ft.Column([
        ft.Row([
            ft.IconButton(
                icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                icon_color=C_TEAL,
                on_click=lambda e: on_back(),
            ),
            ft.Text(
                "📖 القرآن الكريم", size=20, weight=ft.FontWeight.BOLD,
                color=C_TEAL, expand=True, rtl=True,
            ),
        ]),
        ft.Container(content=status_txt, alignment=ft.Alignment(0, 0),
                     width=float("inf")),
        bm_card,
        ft.Divider(color=C_DIVIDER, height=10),
        ft.Row([search_field]),
        ft.Container(content=surah_list, expand=True),
    ], width=W, expand=True)

    view.refresh_bookmark = _refresh_bm
    return view


# ═══════════════════════════════════════════════════════════════════════════════
# AZKAR PAGE  (images loaded lazily on first open)
# ═══════════════════════════════════════════════════════════════════════════════

_azkar_built = False
_azkar_view  = None


def build_azkar_page(on_back, W: float = 400) -> ft.Column:
    def _title(label):
        return ft.Container(
            content=ft.Text(label, size=18, weight="bold", color=C_TEAL, rtl=True),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )

    return ft.Column([
        ft.Row([
            ft.IconButton(
                icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                icon_color=C_TEAL,
                on_click=lambda e: on_back(),
            ),
            ft.Text(
                "📿 الأذكار", size=20, weight=ft.FontWeight.BOLD,
                color=C_TEAL, expand=True, rtl=True,
            ),
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
    ],
        scroll=ft.ScrollMode.AUTO, width=W, expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# RIYAD AL-SALIHIN INDEX
# ═══════════════════════════════════════════════════════════════════════════════

def build_riyad_index(page: ft.Page, on_back, on_open_chapter, W: float = 400):
    chapters = get_all_chapters()

    status_txt = ft.Text(
        f"رياض الصالحين  •  {total_hadiths()} حديث" if riyad_ready()
        else "⏳ جارٍ تحميل الأحاديث...",
        size=12, color=C_TEXT_SUB if riyad_ready() else C_AMBER,
        rtl=True, text_align=ft.TextAlign.CENTER,
    )

    chapter_list = ft.ListView(spacing=4, expand=True)

    def _build_rows(data):
        rows = []
        for c in data:
            def _open(num):
                return lambda e: on_open_chapter(num)
            row = ft.Container(
                content=ft.Row([
                    ft.Container(
                        content=ft.Text(
                            str(c["number"]), size=12, color=C_TEXT,
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        bgcolor=C_AMBER_LT, width=36, height=36,
                        border_radius=18, alignment=ft.Alignment(0, 0),
                    ),
                    ft.Column([
                        ft.Text(c["title_ar"], size=15, color=C_TEXT,
                                weight=ft.FontWeight.BOLD, rtl=True),
                        ft.Text(f"{c['hadith_count']} حديث", size=11, color=C_TEXT_SUB, rtl=True),
                    ], spacing=1, expand=True,
                       horizontal_alignment=ft.CrossAxisAlignment.END),
                    ft.Icon(ft.Icons.CHEVRON_LEFT_ROUNDED, color=C_TEAL, size=18),
                ],
                    alignment=ft.MainAxisAlignment.START,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                bgcolor=C_SURFACE, border_radius=10,
                on_click=_open(c["number"]),
            )
            rows.append(row)
        return rows

    chapter_list.controls = _build_rows(chapters)

    if not chapters:
        def _watch():
            for _ in range(150):
                time.sleep(0.1)
                if riyad_ready():
                    chaps = get_all_chapters()
                    chapter_list.controls = _build_rows(chaps)
                    status_txt.value = f"رياض الصالحين  •  {total_hadiths()} حديث"
                    status_txt.color = C_TEXT_SUB
                    try:
                        chapter_list.update()
                        status_txt.update()
                    except Exception:
                        pass
                    return
        threading.Thread(target=_watch, daemon=True).start()

    return ft.Column([
        ft.Row([
            ft.IconButton(
                icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                icon_color=C_TEAL,
                on_click=lambda e: on_back(),
            ),
            ft.Text(
                "📚 رياض الصالحين", size=20, weight=ft.FontWeight.BOLD,
                color=C_TEAL, expand=True, rtl=True,
            ),
        ]),
        ft.Container(content=status_txt, alignment=ft.Alignment(0, 0),
                     width=float("inf")),
        ft.Divider(color=C_DIVIDER, height=10),
        ft.Container(content=chapter_list, expand=True),
    ], width=W, expand=True)


# ═══════════════════════════════════════════════════════════════════════════════
# RIYAD AL-SALIHIN CHAPTER READER
# ═══════════════════════════════════════════════════════════════════════════════

def build_riyad_chapter(page: ft.Page, chapter_num: int, on_back, W: float = 400):
    chapter = get_chapter(chapter_num)

    hadith_list = ft.ListView(spacing=6, expand=True,
                               padding=ft.Padding.symmetric(horizontal=0, vertical=4))

    if chapter:
        for h in chapter["hadiths"]:
            card = ft.Container(
                content=ft.Column([
                    ft.Container(
                        content=ft.Text(
                            str(h["number"]), size=11, color=C_TEXT,
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        bgcolor=C_AMBER_LT, width=48, height=28,
                        border_radius=14, alignment=ft.Alignment(0, 0),
                    ),
                    ft.Text(
                        h["text_ar"], size=17, color=C_TEXT,
                        text_align=ft.TextAlign.RIGHT,
                        rtl=True, font_family=QURAN_FONT,
                        style=ft.TextStyle(height=2.0),
                    ),
                ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.END),
                padding=ft.Padding.symmetric(horizontal=12, vertical=12),
                bgcolor=C_SURFACE, border_radius=10,
            )
            hadith_list.controls.append(card)
    else:
        hadith_list.controls.append(
            ft.Text("تعذّر تحميل الباب", color=C_DANGER, rtl=True)
        )

    title    = chapter["title_ar"] if chapter else "—"
    subtitle = f"{len(chapter['hadiths'])} حديث" if chapter else ""

    return ft.Column([
        ft.Row([
            ft.IconButton(
                icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                icon_color=C_TEAL,
                on_click=lambda e: on_back(),
            ),
            ft.Text(
                title, size=18, weight=ft.FontWeight.BOLD, color=C_TEAL,
                rtl=True, text_align=ft.TextAlign.CENTER, expand=True,
            ),
        ]),
        ft.Container(
            content=ft.Text(subtitle, size=12, color=C_TEXT_SUB, rtl=True,
                            text_align=ft.TextAlign.CENTER),
            alignment=ft.Alignment(0, 0), width=float("inf"),
        ),
        ft.Divider(color=C_DIVIDER),
        ft.Container(content=hadith_list, expand=True),
    ], width=W, expand=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main(page: ft.Page):
    page.title                = "Salah Reminder"
    page.theme_mode           = ft.ThemeMode.LIGHT
    page.bgcolor              = C_BG
    page.horizontal_alignment = "center"
    page.padding              = 20

    # ── Responsive width ──────────────────────────────────────────────────────
    # page.width is available immediately on mobile (flet build).
    # On desktop it may be 0 briefly; we default to 400 and it's recomputed
    # each time a page is built (lazy), so late opens always get correct width.
    W = _W(page)

    # ── Font ──────────────────────────────────────────────────────────────────
    # "AmiriQuran.ttf" resolves against assets_dir on desktop and the Flutter
    # asset bundle on mobile — no platform branching needed.
    _platform  = getattr(page, "platform", None)
    _is_mobile = _platform in ("android", "ios")
    page.fonts = {
        "AmiriQuran": "AmiriQuran.ttf",
    }

    page.theme = ft.Theme(color_scheme=ft.ColorScheme(
        primary=C_TEAL, secondary=C_AMBER_DK,
        surface=C_SURFACE, on_primary="white", on_surface=C_TEXT,
    ))

    # ── Azan player ───────────────────────────────────────────────────────────
    azan = AzanPlayer()

    # ── State — all loaded from disk (fast, no network) ───────────────────────
    city, country, saved_method = load_location()
    if not city:
        city, country, saved_method = "Nicosia", "Cyprus", "General (MWL)"

    _settings     = load_settings()
    _saved_volume = float(_settings.get("azan_volume", 1.0))
    azan.set_volume(_saved_volume)

    completed_prayers = load_completed_prayers()
    notified_prayers  = set()
    current_day       = datetime.now().date()
    _lock             = threading.Lock()

    # ── Prayer times: serve from cache instantly, fetch in bg if stale ────────
    prayer_times = get_prayer_times_best(city, country, saved_method)

    # If cache miss (first ever run), show skeleton and fetch in background
    _times_ready = [bool(prayer_times)]

    _prayer_btns:  dict[str, ft.FilledButton] = {}
    _prayer_cards: dict[str, ft.Container]    = {}
    _deb_save     = Debounce(0.5, save_completed_prayers)

    # ── Page stack ────────────────────────────────────────────────────────────
    page_stack   = ft.Stack(expand=True)
    _active_page = [0]

    # Lazy page containers — content set on first visit
    _lazy_slots: dict[str, ft.Container | None] = {
        "quran":        None,
        "surah":        None,
        "azkar":        None,
        "riyad":        None,
        "riyad_ch":     None,
    }

    def _show(idx: int):
        if _active_page[0] == idx:
            return
        page_stack.controls[_active_page[0]].visible = False
        page_stack.controls[idx].visible             = True
        _active_page[0] = idx
        if idx == 1:
            try:
                _lazy_slots["quran_view"].refresh_bookmark()
            except Exception:
                pass
        page.update()

    def go_home(e=None):   _show(0)

    def go_quran(e=None):
        # Build quran index lazily on first open
        slot = page_stack.controls[1]
        if slot.content is None:
            qv = build_quran_index(page, on_back=go_home, on_open_surah=go_surah, W=W)
            _lazy_slots["quran_view"] = qv
            slot.content = qv
        else:
            try:
                _lazy_slots["quran_view"].refresh_bookmark()
            except Exception:
                pass
        _show(1)

    def go_azkar(e=None):
        slot = page_stack.controls[3]
        if slot.content is None:
            slot.content = build_azkar_page(on_back=go_home, W=W)
        _show(3)

    def go_riyad(e=None):
        slot = page_stack.controls[4]
        if slot.content is None:
            slot.content = build_riyad_index(
                page, on_back=go_home, on_open_chapter=go_riyad_chapter, W=W
            )
        _show(4)

    def go_riyad_chapter(number: int):
        ch = build_riyad_chapter(page, number, on_back=lambda: _show(4), W=W)
        page_stack.controls[5].content = ch
        _show(5)

    def go_surah(number: int):
        bm      = load_bookmark()
        bm_ayah = bm["ayah"] if (bm and bm.get("surah") == number) else None

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

        reader, ayah_list, _ = build_surah_reader(
            page, number, on_back=lambda: _show(1), bookmark=bm, W=W,
        )
        page_stack.controls[2].content = reader
        _show(2)
        if bm_ayah:
            _scroll_to_ayah(ayah_list, bm_ayah)

    def _scroll_to_ayah(ayah_list: ft.ListView, ayah_num: int):
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

    # ── Prayer cards ──────────────────────────────────────────────────────────
    cards_col = ft.Column(spacing=12, width=W)

    # Skeleton shown when no cache exists on first launch
    _skeleton_card = ft.Container(
        content=ft.Row([
            ft.ProgressRing(width=20, height=20, color=C_TEAL),
            ft.Text("  جارٍ تحميل مواقيت الصلاة...", color=C_TEXT_SUB, rtl=True),
        ], alignment=ft.MainAxisAlignment.CENTER),
        padding=ft.Padding.symmetric(horizontal=16, vertical=14),
        bgcolor=C_SURFACE, border_radius=12, width=W,
    )

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

        if not times:
            # Show skeleton — checker will call build_prayer_cards again when ready
            cards_col.controls.append(_skeleton_card)
            try:
                cards_col.update()
            except Exception:
                pass
            return

        for name, ptime in times.items():
            done = name in completed_prayers
            btn = ft.FilledButton(
                ft.Text("Done ✔" if done else "Done",
                        color="white" if done else C_TEXT),
                style=_btn(C_TEAL if done else C_AMBER_LT,
                           "white" if done else C_TEXT),
                height=35, on_click=_toggle(name),
            )
            _prayer_btns[name] = btn
            card = ft.Container(
                content=ft.Row([
                    ft.Container(
                        width=5, height=54, bgcolor=C_TEAL,
                        border_radius=ft.BorderRadius(4, 0, 0, 4),
                    ),
                    ft.Container(
                        ft.Text(name, size=18, weight="bold", color=C_TEXT),
                        width=110, padding=ft.Padding.only(left=8),
                    ),
                    ft.Container(expand=True),  # left spacer
                    ft.Container(
                        ft.Text(ptime, size=17, color=C_AMBER),
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Container(expand=True),  # right spacer
                    btn,
                ],
                    alignment=ft.MainAxisAlignment.START,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=0,
                ),
                padding=ft.Padding.only(right=16, top=10, bottom=10),
                bgcolor=C_TEAL if done else C_TEAL_LT,
                border_radius=12,
            )
            _prayer_cards[name] = card
            cards_col.controls.append(card)
        try:
            page.update()
        except Exception:
            pass

    # ── Azan UI panel ─────────────────────────────────────────────────────────
    _vol_icon = lambda p: "🔊" if p > 50 else ("🔉" if p > 0 else "🔇")
    pct0 = int(azan.get_volume() * 100)

    vol_label = ft.Text(f"{_vol_icon(pct0)}  {pct0}%", size=13, color=C_TEXT_SUB, width=70)

    stop_btn = ft.FilledButton(
        "⏹  إيقاف الأذان",
        style=_btn(C_DANGER, "white"),
        height=34, visible=False,
    )

    def _refresh_stop_btn():
        playing = azan.is_playing()
        if stop_btn.visible != playing:
            stop_btn.visible = playing
            try:
                stop_btn.update()
            except Exception:
                pass

    stop_btn.on_click = lambda e: (azan.stop(), _refresh_stop_btn())

    _deb_save_vol = Debounce(0.5, lambda v: save_settings({"azan_volume": v}))

    def _on_vol_change(e):
        v   = float(e.control.value) / 100.0
        pct = int(v * 100)
        vol_label.value = f"{_vol_icon(pct)}  {pct}%"
        vol_label.update()
        azan.set_volume(v)
        _deb_save_vol(v)

    vol_slider = ft.Slider(
        min=0, max=100, value=pct0, divisions=20,
        active_color=C_TEAL, inactive_color=C_DIVIDER,
        thumb_color=C_AMBER_DK, expand=True,
        label="{value}%", on_change=_on_vol_change,
    )

    azan_panel = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Icon(ft.Icons.VOLUME_UP_ROUNDED, color="white", size=18),
                    bgcolor=C_TEAL, width=34, height=34,
                    border_radius=8, alignment=ft.Alignment(0, 0),
                ),
                ft.Text(
                    "أذان مشاري راشد العفاسي", size=14,
                    weight=ft.FontWeight.BOLD, color=C_TEXT, rtl=True, expand=True,
                ),
                vol_label,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([vol_slider], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([stop_btn], alignment=ft.MainAxisAlignment.CENTER),
        ], spacing=4),
        bgcolor=C_SURFACE, border_radius=12,
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        width=W,
    )

    # ── Notifications + azan trigger ──────────────────────────────────────────
    def send_notification(prayer: str):
        print(f"[Salah] 🕌 {prayer}")

        send_android_notification("🕌 وقت الصلاة", f"حان وقت صلاة {prayer}")

        try:
            page.snack_bar = ft.SnackBar(
                ft.Text(f"حان وقت {prayer}! 🕌", color="white"),
                bgcolor=C_TEAL,
            )
            page.snack_bar.open = True
            page.update()
        except Exception as exc:
            print(f"[Salah] snack: {exc}")

        if azan.ok and azan.get_volume() > 0:
            azan.play(prayer)
            azan.set_on_stop(_refresh_stop_btn)
            threading.Thread(
                target=lambda: (time.sleep(0.5), _refresh_stop_btn()),
                daemon=True,
            ).start()

    # ── Location settings ─────────────────────────────────────────────────────
    location_label = ft.Text(
        f"📍 {city}, {country}", size=14, color=C_TEXT_SUB,
        text_align=ft.TextAlign.CENTER,
    )
    city_input = ft.TextField(
        label="City", width=W/2 - 6,
        label_style=ft.TextStyle(color=C_TEXT_SUB),
        color=C_TEXT, bgcolor=C_SURFACE,
        border_color=C_DIVIDER, focused_border_color=C_TEAL,
    )
    country_input = ft.TextField(
        label="Country", width=W/2 - 6,
        label_style=ft.TextStyle(color=C_TEXT_SUB),
        color=C_TEXT, bgcolor=C_SURFACE,
        border_color=C_DIVIDER, focused_border_color=C_TEAL,
    )
    method_dd = ft.Dropdown(
        label="Prayer Calculation Method", value=saved_method,
        options=[ft.dropdown.Option(n) for n in PRAYER_METHODS.keys()],
        width=W, bgcolor=C_SURFACE, color=C_TEXT,
        border_color=C_DIVIDER, focused_border_color=C_TEAL,
    )

    def _set_location(e):
        nonlocal city, country, prayer_times
        nc = city_input.value.strip()
        nr = country_input.value.strip()
        if not nc or not nr:
            return
        city, country = nc, nr
        save_location(city, country, method_dd.value)
        location_label.value = f"📍 {city}, {country}"
        location_label.update()

        # Show skeleton immediately
        with _lock:
            prayer_times = {}
        build_prayer_cards()

        def _fetch():
            nonlocal prayer_times
            try:
                os.remove(PRAYER_CACHE_FILE)
            except Exception:
                pass
            times = get_prayer_times(city, country, method_dd.value)
            if times:
                _write_json(PRAYER_CACHE_FILE, {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "city": city, "country": country,
                    "method": method_dd.value, "times": times,
                })
            with _lock:
                prayer_times = times
            build_prayer_cards()

        _BG.submit(_fetch)

    # ── Main page layout ───────────────────────────────────────────────────────
    main_page = ft.Column([
        ft.Container(
            content=ft.Text(
                "🕌 Salah Reminder", size=24,
                weight=ft.FontWeight.BOLD, color="white",
                text_align=ft.TextAlign.CENTER,
            ),
            bgcolor=C_TEAL,
            border_radius=ft.BorderRadius(0, 0, 16, 16),
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            width=float("inf"),
        ),
        ft.Container(height=8),
        ft.Row([city_input, country_input], alignment=ft.MainAxisAlignment.CENTER),
        method_dd,
        ft.FilledButton(
            "📍 Set Location",
            style=_btn(C_AMBER_DK, "white"),
            on_click=_set_location,
        ),
        location_label,
        ft.Divider(color=C_DIVIDER),
        cards_col,
        ft.Divider(color=C_DIVIDER),
        azan_panel,
        ft.Divider(color=C_DIVIDER),
        ft.FilledButton(
            "📖 القرآن الكريم",
            style=_btn(C_TEAL, "white"),
            width=W, height=44,
            on_click=go_quran,
        ),
        ft.FilledButton(
            "📿 الأذكار",
            style=_btn(C_AMBER_DK, "white"),
            width=W, height=44,
            on_click=go_azkar,
        ),
        ft.FilledButton(
            "📚 رياض الصالحين",
            style=_btn(C_TEAL, "white"),
            width=W, height=44,
            on_click=go_riyad,
        ),
    ],
        width=W,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        scroll=ft.ScrollMode.AUTO,
    )

    # ── Stack: slots 1-5 start with NO content (built lazily) ─────────────────
    page_stack.controls = [
        ft.Container(content=main_page, visible=True,  expand=True, bgcolor=C_BG),
        ft.Container(content=None,      visible=False, expand=True, bgcolor=C_BG),  # quran index
        ft.Container(content=None,      visible=False, expand=True, bgcolor=C_BG),  # surah reader
        ft.Container(content=None,      visible=False, expand=True, bgcolor=C_BG),  # azkar
        ft.Container(content=None,      visible=False, expand=True, bgcolor=C_BG),  # riyad index
        ft.Container(content=None,      visible=False, expand=True, bgcolor=C_BG),  # riyad chapter
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # CRITICAL ORDER:
    #   1. page.add()          — renders the frame immediately
    #   2. build_prayer_cards()— populates cards from cache (fast)
    #   3. azan.init()         — deferred internally (non-blocking)
    #   4. request perms       — asks Android 13+ for notifications
    #   5. handle_back         — intercepts Android back button
    # ══════════════════════════════════════════════════════════════════════════
    page.add(page_stack)
    build_prayer_cards()
    azan.init(page)
    request_notification_permission()

    # ── Android hardware/gesture back button ──────────────────────────────────
    def handle_back(e):
        idx = _active_page[0]
        if idx == 0:
            # Already on home — let the app minimize/close normally
            e.prevent_default = False
            page.update()
        elif idx == 2:
            # Surah reader → back to Quran index
            _show(1)
            e.prevent_default = True
            page.update()
        elif idx == 5:
            # Riyad chapter → back to Riyad index
            _show(4)
            e.prevent_default = True
            page.update()
        else:
            # Quran index (1), Azkar (3), Riyad index (4) → back to home
            _show(0)
            e.prevent_default = True
            page.update()

    page.on_back = handle_back

    # ── If no cache, fetch prayer times in background and update cards ─────────
    if not prayer_times:
        def _initial_fetch():
            nonlocal prayer_times
            times = get_prayer_times(city, country, saved_method)
            if times:
                _write_json(PRAYER_CACHE_FILE, {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "city": city, "country": country,
                    "method": saved_method, "times": times,
                })
            with _lock:
                prayer_times = times
            build_prayer_cards()
        _BG.submit(_initial_fetch)

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


ft.app(main, assets_dir="assets")