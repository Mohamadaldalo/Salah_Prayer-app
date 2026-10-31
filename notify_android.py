"""
notify_android.py
=================
Android notification helper for Salah Reminder.

Drop this file next to main.py.  It is only ever imported on Android
(guarded by the try/except jnius check in main.py), so it is completely
safe to ship — it does nothing on desktop.

Requirements (already in pyproject.toml):
    [tool.flet.android]
    dependencies = ["pyjnius", "android-notify==1.60.10.dev0"]

    [tool.flet.android.permission]
    "android.permission.POST_NOTIFICATIONS" = true
"""

import threading


def request_notification_permission():
    """
    Ask Android 13+ for POST_NOTIFICATIONS at runtime.
    Uses android_notify's built-in helper which handles the API-level check
    automatically — safe to call on any Android version.
    Runs in a background thread so it never blocks the UI.
    """
    def _ask():
        try:
            from android_notify.core import asks_permission_if_needed  # type: ignore
            asks_permission_if_needed()
            print("[Notify] permission check done")
        except Exception as exc:
            print(f"[Notify] permission request failed: {exc}")

    threading.Thread(target=_ask, daemon=True).start()


def send_android_notification(title: str, message: str):
    """
    Show a system notification banner + sound on Android.
    Safe to call from any thread (including the background checker).
    """
    def _send():
        try:
            from android_notify import Notification  # type: ignore
            Notification(title=title, message=message).send()
            print(f"[Notify] sent: {title!r}")
        except Exception as exc:
            print(f"[Notify] send failed: {exc}")

    threading.Thread(target=_send, daemon=True).start()