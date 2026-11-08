"""Control window: On/Off, Show all rows, Restart as admin."""
from __future__ import annotations

import ctypes
import logging
import sys

from mapletool.winutil import SingleInstance

log = logging.getLogger("mapletool")

TITLE = "MapleStory Classic Tool"
ALREADY_RUNNING = "MapleStory Classic Tool is already running."


def status_text(state) -> str:
    kind = state[0]
    if kind == "off":
        return "Off"
    if kind == "starting":
        return "Starting…"
    if kind == "waiting":
        return "On — waiting for game"
    if kind == "no_shop":
        return "On — no shop open"
    if kind == "shop":
        return f"On — shop found ({state[1]} deals)"
    if kind == "no_data":
        return "No price data"
    return str(kind)


def initial_checks(settings) -> dict:
    """Checkbox values shown when the control window opens."""
    return {"show_all": bool(settings.show_all), "exp_tracker": bool(settings.exp_tracker),
            "block_alt_shift": bool(settings.block_alt_shift)}


def button_states(state) -> dict:
    off = state[0] == "off"
    return {"on": off, "off": not off}


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def relaunch_args() -> tuple[str, str]:
    """(executable, parameters) that relaunch this app: frozen exe, or python -m mapletool --gui."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    return sys.executable, "-m mapletool --gui"


def restart_as_admin() -> bool:
    exe, params = relaunch_args()
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params or None, None, 1)
    return rc > 32


def icon_path():
    from mapletool.live import ASSETS
    return ASSETS / "app.ico"


def set_window_icon(root) -> None:
    """The app icon on the control window (title bar, taskbar) and its dialogs instead of Tk's feather."""
    try:
        root.iconbitmap(default=str(icon_path()))
    except Exception:  # noqa: BLE001
        log.warning("could not set the window icon", exc_info=True)


def restore_shortcut(blocker) -> None:
    """Give Windows its Alt+Shift shortcut back (on exit; never raises)."""
    if blocker is None:
        return
    try:
        blocker.restore()
    except Exception:  # noqa: BLE001
        log.exception("could not restore the language shortcut")


def notify_already_running() -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showinfo(TITLE, ALREADY_RUNNING, parent=root)
    finally:
        root.destroy()


class ControlPanel:
    def __init__(self, root, session, admin: bool | None = None, instance=None, blocker=None):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.session = session
        self.instance = instance  # SingleInstance held by this process (released for "Restart as admin")
        self.blocker = blocker  # langhotkey.LangHotkeyBlocker (Alt+Shift off while the app is open)
        root.title(TITLE)
        root.resizable(False, False)
        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value=status_text(session.state))
        ttk.Label(frm, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(frm)
        row.pack(fill="x")
        self.on_btn = ttk.Button(row, text="On", command=self._on)
        self.off_btn = ttk.Button(row, text="Off", command=self._off)
        self.on_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.off_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))
        self.show_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Show all rows", variable=self.show_all_var,
                        command=self._show_all).pack(anchor="w", pady=(8, 0))
        self.exp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="EXP tracker", variable=self.exp_var,
                        command=self._exp_tracker).pack(anchor="w", pady=(2, 0))
        self.alt_shift_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Block Alt+Shift language switch", variable=self.alt_shift_var,
                        command=self._block_alt_shift).pack(anchor="w", pady=(2, 0))
        if admin is None:
            admin = is_admin()
        if not admin:
            ttk.Label(frm, text="F8/F9 need admin", foreground="#a00").pack(anchor="w", pady=(8, 0))
            ttk.Button(frm, text="Restart as admin", command=self._restart).pack(fill="x", pady=(2, 0))
        session.on_state = self._render
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._render(session.state)
        root.after(16, self._pump)

    def _render(self, state) -> None:
        self.status_var.set(status_text(state))
        bs = button_states(state)
        self.on_btn.state(["!disabled"] if bs["on"] else ["disabled"])
        self.off_btn.state(["!disabled"] if bs["off"] else ["disabled"])

    def _pump(self) -> None:
        try:
            self.session.pump()
        except Exception:  # noqa: BLE001
            log.exception("ui pump error")
        try:
            self.root.after(16, self._pump)
        except Exception:  # noqa: BLE001
            pass  # root destroyed

    def _on(self) -> None:
        self._render(("starting",))
        self.root.update_idletasks()
        try:
            self.session.start()
        except Exception:  # noqa: BLE001
            log.exception("start failed")
            from tkinter import messagebox
            messagebox.showerror(TITLE, "Could not start scanning. See log.txt in %LOCALAPPDATA%\\MapleTool.")

    def _off(self) -> None:
        self.session.stop()

    def _show_all(self) -> None:
        self.session.set_show_all(self.show_all_var.get())

    def _exp_tracker(self) -> None:
        self.session.set_exp_tracker(self.exp_var.get())

    def _block_alt_shift(self) -> None:
        value = bool(self.alt_shift_var.get())
        blocker = getattr(self, "blocker", None)
        if blocker is not None:
            try:
                blocker.block() if value else blocker.restore()
            except Exception:  # noqa: BLE001
                log.exception("could not change the language shortcut")
        self.session.set_block_alt_shift(value)

    def _restart(self) -> None:
        instance = getattr(self, "instance", None)
        if instance is not None:
            instance.release()  # the elevated copy must not find this copy's mutex
        if restart_as_admin():
            self.session.close()
            self.root.destroy()
        else:
            log.warning("restart as admin was cancelled or failed")
            if instance is not None and not instance.acquire():
                log.warning("single-instance lock was taken by another copy meanwhile")

    def close(self) -> None:
        restore_shortcut(getattr(self, "blocker", None))
        self.session.close()  # release OCR/dxcam before Tk teardown and interpreter exit
        self.root.destroy()


def main() -> int:
    from mapletool.live import _setup_logging, set_dpi_aware
    set_dpi_aware()
    _setup_logging()
    instance = SingleInstance()
    if not instance.acquire():
        log.warning("another copy is already running; exiting")
        notify_already_running()
        return 1
    import tkinter as tk

    from mapletool.config import Settings, data_dir, load_settings
    from mapletool.langhotkey import LangHotkeyBlocker
    from mapletool.overlay import Overlay
    from mapletool.session import LiveSession

    root = tk.Tk()
    root.withdraw()  # no empty-window flash while widgets are built
    set_window_icon(root)

    def report(exc, val, tb):
        log.error("Tk callback exception", exc_info=(exc, val, tb))

    root.report_callback_exception = report
    overlay = Overlay(tk.Toplevel(root))
    session = LiveSession(root, overlay)
    try:
        settings = load_settings(data_dir() / "settings.json")
    except Exception:  # noqa: BLE001
        log.exception("could not read settings")
        settings = Settings()
    checks = initial_checks(settings)
    blocker = LangHotkeyBlocker(data_dir() / "lang_hotkey_backup.json")
    try:
        blocker.recover()  # a previous run that crashed left Alt+Shift off
        if checks["block_alt_shift"]:
            blocker.block()
    except Exception:  # noqa: BLE001
        log.exception("could not change the language shortcut")
    panel = ControlPanel(root, session, instance=instance, blocker=blocker)
    panel.show_all_var.set(checks["show_all"])
    panel.exp_var.set(checks["exp_tracker"])
    panel.alt_shift_var.set(checks["block_alt_shift"])
    root.update_idletasks()
    root.deiconify()
    try:
        root.mainloop()
    finally:
        restore_shortcut(blocker)
        session.close()
        instance.release()
    return 0
