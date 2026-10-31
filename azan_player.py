"""
azan_player.py — cross-platform audio (iOS, Android, Desktop)
Uses flet_audio only (Flet 0.80+). No pygame dependency.

Setup: call azan_player.init_flet_audio(page) inside main(page)

Audio files:
    assets/azan/fajr.mp3    <- Fajr
    assets/azan/normal.mp3  <- All other prayers
"""

import os
import threading

# ── flet_audio ───────────────────────────────────────────────────────────────
try:
    import flet_audio as fta
    _FLET_AUDIO_PKG = True
except Exception:
    _FLET_AUDIO_PKG = False

# ── state ────────────────────────────────────────────────────────────────────
_lock        = threading.Lock()
_playing     = False
_current_vol = 1.0
_on_stop_cb  = None
_audio_ctrl  = None   # flet_audio Audio control, set by init_flet_audio()
_page        = None

FAJR_PRAYERS = {"Fajr"}


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _assets_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "azan")


def _has_files() -> bool:
    d = _assets_dir()
    try:
        return os.path.isdir(d) and any(f.endswith(".mp3") for f in os.listdir(d))
    except Exception:
        return False


def _pick_asset_rel(prayer: str) -> str | None:
    """Return asset-relative path for flet_audio (e.g. 'azan/normal.mp3')"""
    d = _assets_dir()
    if prayer in FAJR_PRAYERS and os.path.exists(os.path.join(d, "fajr.mp3")):
        return "azan/fajr.mp3"
    if os.path.exists(os.path.join(d, "normal.mp3")):
        return "azan/normal.mp3"
    try:
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".mp3"):
                return f"azan/{fn}"
    except Exception:
        pass
    return None


def _on_state_changed(e):
    global _playing
    state = str(e.data) if hasattr(e, "data") else str(e)
    if state in ("stopped", "completed"):
        with _lock:
            _playing = False
        cb = _on_stop_cb
        if cb:
            try:
                cb()
            except Exception:
                pass
    elif state == "playing":
        with _lock:
            _playing = True


# ═══════════════════════════════════════════════════════════════════════════════
# INIT — call once from main(page)
# ═══════════════════════════════════════════════════════════════════════════════

def init_flet_audio(page):
    """
    Call inside main(page) right after page properties are set:
        import azan_player
        azan_player.init_flet_audio(page)
    """
    global _audio_ctrl, _page

    if not _FLET_AUDIO_PKG:
        print("[azan] flet_audio package not installed — audio disabled")
        return

    _page = page
    try:
        _audio_ctrl = fta.Audio(
            src="azan/normal.mp3",
            volume=_current_vol,
            on_state_changed=_on_state_changed,
        )
        page.overlay.append(_audio_ctrl)
        page.update()
        print("[azan] flet_audio initialised ✅")
    except Exception as exc:
        print(f"[azan] flet_audio init failed: {exc} — audio disabled")
        _audio_ctrl = None


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def is_available() -> bool:
    return _audio_ctrl is not None and _has_files()


def is_playing() -> bool:
    with _lock:
        return _playing


def set_on_stop(callback):
    global _on_stop_cb
    _on_stop_cb = callback


def play(prayer: str):
    if _audio_ctrl is None:
        print("[azan] audio not available")
        return

    stop()

    src = _pick_asset_rel(prayer)
    if not src:
        print(f"[azan] no audio file found for '{prayer}'")
        return

    def _play():
        global _playing
        try:
            _audio_ctrl.src    = src
            _audio_ctrl.volume = _current_vol
            _audio_ctrl.update()
            _audio_ctrl.play()
            with _lock:
                _playing = True
        except Exception as exc:
            print(f"[azan] play error: {exc}")

    threading.Thread(target=_play, daemon=True).start()


def stop():
    global _playing
    if _audio_ctrl is not None:
        try:
            _audio_ctrl.pause()
        except Exception:
            pass
    with _lock:
        _playing = False


def set_volume(vol: float):
    global _current_vol
    _current_vol = max(0.0, min(1.0, vol))

    if _audio_ctrl is not None:
        try:
            _audio_ctrl.volume = _current_vol
            _audio_ctrl.update()
        except Exception:
            pass

    if _current_vol == 0.0:
        stop()


def get_volume() -> float:
    return _current_vol