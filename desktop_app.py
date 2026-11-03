# -*- coding: utf-8 -*-
"""桌面工具：左侧配置菜单 + 右侧独立汇总模板。"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import tkinter as tk
import copy
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

# 源码版也沿用已安装版本的配置、服务账号和运行状态。这样无论通过
# 启动脚本还是直接执行 desktop_app.py，都不会误读源码目录中的空配置。
if not os.environ.get("SHEETS_POST_FILTER_DATA_DIR"):
    if sys.platform == "darwin":
        _installed_data_dir = Path.home() / "Library" / "Application Support" / "sheets-post-filter"
        _installed_data_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SHEETS_POST_FILTER_DATA_DIR"] = str(_installed_data_dir)
    else:
        _installed_data_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "sheets-post-filter"
        if (_installed_data_dir / "config.json").exists():
            os.environ["SHEETS_POST_FILTER_DATA_DIR"] = str(_installed_data_dir)

from fetch_posts import (
    LOG_DIR,
    RESOURCE_DIR,
    SCRIPT_DIR,
    Config,
    authorize,
    copy_default_fields,
    discover_credential_files,
    load_config,
    normalize_credential_paths,
    peek_source_headers,
    save_config,
    service_account_email,
    write_app_log,
)
from dataclasses import asdict
from template_registry import TEMPLATES, TEMPLATE_NAMES, partition_menus, new_template_settings
from worksheet_merge import read_merge_headers
from desktop_theme import COLORS as C, ColorButton, install_theme

from app import (
    _align_schedule_snapshot,
    _catalog_schedule_snapshot,
    _cfg_from_payload,
    job_snapshot,
    menu_schedule_snapshot,
    _schedule_snapshot,
    _video_schedule_snapshot,
    start_align_job,
    start_catalog_job,
    start_catalog_scheduler,
    start_posts_job,
    _posts_schedule_snapshot,
    start_roster_job,
    start_sheet_metrics_job,
    start_worksheet_merge_job,
    start_worksheet_publish_job,
    start_worksheet_links_job,
    start_worksheet_sync_job,
    start_align_scheduler,
    start_filter_job,
    start_publish_job,
    start_scheduler,
    start_video_job,
    start_video_scheduler,
    stop_align_scheduler,
    stop_all_jobs,
    stop_all_menu_schedulers,
    stop_catalog_scheduler,
    stop_job,
    stop_scheduler,
    stop_video_scheduler,
    sync_schedulers_from_menus,
)
from version import APP_VERSION, RELEASES_URL, UPDATE_API_URL, version_tuple
from promo_deploy import (
    TOKEN_CREATE_URL,
    CloudflareAPI,
    CloudflareError,
    deploy_gallery,
    known_gallery_sites,
    load_cf_state,
    login_with_token,
    normalize_host,
)

MUTEX_PORT = 18765
_UI_FONT = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI"
_MONO_FONT = "Menlo" if sys.platform == "darwin" else "Cascadia Mono"
F = (_UI_FONT, 12 if sys.platform == "darwin" else 10)
FS = (_UI_FONT, 11 if sys.platform == "darwin" else 9)
FB = (_UI_FONT, 13 if sys.platform == "darwin" else 11, "bold")
FH = (_UI_FONT, 18 if sys.platform == "darwin" else 16, "bold")
MONO = (_MONO_FONT, 11 if sys.platform == "darwin" else 9)


def _scroll_step(delta: int) -> int:
    if not delta:
        return 0
    return (-1 if delta > 0 else 1) * max(1, abs(int(delta)) // 120)


def _open_folder(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif hasattr(os, "startfile"):
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _mutex_socket() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", MUTEX_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def _bring_existing_window() -> bool:
    """Restore an already-running instance instead of silently exiting.

    Title-only matching is not enough: Explorer often has a folder named
    「数据汇总工具」(the dist directory). Restoring that folder made a second
    launch look like the app opened and immediately quit.
    """
    try:
        with socket.create_connection(('127.0.0.1', MUTEX_PORT), timeout=0.6) as existing:
            existing.sendall(b'sheets-summary:show-window:v1')
            if existing.recv(64) == b'sheets-summary:window-restored:v1':
                return True
    except OSError:
        pass
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        GetClassNameW = user32.GetClassNameW
        GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        found = []

        def cb(hwnd, _lparam):
            title = ctypes.create_unicode_buffer(256)
            GetWindowTextW(hwnd, title, 256)
            if title.value != "数据汇总工具":
                return True
            cls = ctypes.create_unicode_buffer(256)
            GetClassNameW(hwnd, cls, 256)
            # Tk windows are TkTopLevel; Explorer folders are CabinetWClass.
            if cls.value == "TkTopLevel":
                found.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_SHOWWINDOW = 0x0040
        for hwnd in found:
            user32.ShowWindow(hwnd, 9)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 80, 40, 1200, 840, SWP_SHOWWINDOW)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 80, 40, 1200, 840, SWP_SHOWWINDOW)
        return bool(found)
    except Exception:
        return False


def _pids_listening_on_mutex() -> list[int]:
    pids: list[int] = []
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return pids
    needle = f"127.0.0.1:{MUTEX_PORT}"
    for line in out.splitlines():
        if needle not in line:
            continue
        upper = line.upper()
        if "LISTENING" not in upper and "LISTEN" not in upper:
            continue
        try:
            pids.append(int(line.split()[-1]))
        except ValueError:
            continue
    return pids


def _process_name(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return ""
    text = out.strip()
    if not text or text.lower().startswith("info:"):
        return ""
    return text.strip('"').split(",")[0].strip('"')


def _is_own_python_process(pid: int) -> bool:
    """A shared port and python.exe name do not prove process ownership."""
    import shlex
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"
    )
    try:
        output = subprocess.check_output(
            [str(shell), "-NoProfile", "-NonInteractive", "-Command", command],
            stderr=subprocess.DEVNULL, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).decode("utf-8-sig", errors="replace").strip()
        wanted = (RESOURCE_DIR / "desktop_app.py").resolve()
        return any(Path(arg.strip('"')).resolve() == wanted for arg in shlex.split(output, posix=False))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _kill_stale_mutex() -> bool:
    """Kill a leftover instance that holds the single-instance port but has no window."""
    killed = False
    my_pid = os.getpid()
    for pid in _pids_listening_on_mutex():
        if pid == my_pid:
            continue
        name = _process_name(pid)
        if "数据汇总工具" not in name and name.lower() not in ("python.exe", "pythonw.exe"):
            continue
        if name.lower() in ("python.exe", "pythonw.exe") and not _is_own_python_process(pid):
            continue
        try:
            subprocess.check_call(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            killed = True
        except Exception:
            continue
    return killed


class StyleBtn(ColorButton):
    def __init__(self, master, kind="ghost", **kw):
        if kind == "primary":
            defaults = dict(bg=C["accent"], fg="white", activebackground="#1d4ed8", activeforeground="white", bd=0)
        elif kind == "head":
            defaults = dict(bg=C["head"], fg=C["cream"], activebackground="#274b79", activeforeground=C["cream"], bd=0)
        else:
            defaults = dict(
                bg=C["card"],
                fg=C["ink"],
                activebackground="#e2e8f0",
                activeforeground=C["ink"],
                bd=1,
                relief="solid",
                highlightthickness=0,
            )
        defaults.update(
            font=F,
            padx=10,
            pady=6,
            cursor="hand2",
            highlightbackground=C["line"],
        )
        defaults.update(kw)
        super().__init__(master, **defaults)


class DesktopApp(tk.Tk):
    def __init__(self) -> None:
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.secure-artifacts.sheets-post-filter")
            except Exception:
                pass
        for _mod_dir in (str(Path(__file__).resolve().parent), str(RESOURCE_DIR)):
            if _mod_dir and _mod_dir not in sys.path:
                sys.path.insert(0, _mod_dir)
        super().__init__()
        os.chdir(SCRIPT_DIR)
        self._logo_full: tk.PhotoImage | None = None
        self._logo: tk.PhotoImage | None = None
        self.cfg = load_config()
        self._log_n = 0
        self._src_rows: list[tk.Frame] = []
        self._align_rows: list[tk.Frame] = []
        self._field_rows: list[tk.Frame] = []
        self._vd_type_rows: list[tk.Frame] = []
        self._vd_exclude_rows: list[tk.Frame] = []
        self._vd_report_rows: list[tk.Frame] = []
        self._vd_duration_rule_rows: list[tk.Frame] = []
        self._vd_extra_col_rows: list[tk.Frame] = []
        self._catalog_exclude_rows: list[tk.Frame] = []
        self._posts_col_rows: list[tk.Frame] = []
        self._wm_column_rows: list[tk.Frame] = []
        self._alive = True
        self._tick_id = None
        self._vd_ui_mode = ""
        self._menus: list[dict] = []
        self._menu_buttons: dict[str, StyleBtn] = {}
        self._active_menu_id = ""
        self._selecting_menu = False
        self._scroll_pages: list[tk.Frame] = []
        self._align_profiles: dict[str, list[dict]] = {}
        self._align_profile_key = "__default__"
        self._align_default_mappings: list[dict] = []
        self.var_credentials = tk.StringVar()
        self.var_sa = tk.StringVar(value="未找到服务账号")
        self.sa_hint = tk.StringVar(value="源表和目标表都要共享给每一个账号 · 编辑者")
        self._cred_files: list[str] = []
        self._settings_win: tk.Toplevel | None = None
        self._tab = ""
        self._tick_cache: dict[str, str] = {}
        self._sa_files_shown: list[str] | None = None
        self._pending_select: dict | None = None
        self._page_settings: dict[str, dict] = {}
        self._menu_scroll: dict[str, float] = {}
        self._selection_after = None
        self.title("数据汇总工具")
        self.geometry("1180x820+80+40")
        self.minsize(980, 680)
        self.configure(bg=C["paper"])
        install_theme(self, F)
        self.bind_all("<MouseWheel>", self._wheel_page, add="+")
        self.bind("<Control-s>", lambda _event: self._save())
        self._apply_window_icon()
        self._build()
        self._init_menus()
        self._load_global_credentials()
        self._sync_schedulers()
        self._tick_id = self.after(300, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == 'darwin':
            # Finder / Dock reopen events must restore the existing Tk window.
            self.createcommand('::tk::mac::ReopenApplication', self.show_main_window)

        def _tk_exc(exc, val, tb):
            err = "".join(traceback.format_exception(exc, val, tb))
            try:
                write_app_log("界面异常", err)
                (SCRIPT_DIR / "desktop_error.log").write_text(err, encoding="utf-8")
            except Exception:
                pass

        self.report_callback_exception = _tk_exc

    def show_main_window(self) -> None:
        self.deiconify()
        self.lift()
        if sys.platform == 'darwin':
            # Aqua may create a valid window while keeping the app behind Finder.
            try:
                self.focus_force()
            except tk.TclError:
                pass
        else:
            self.focus_set()

    # ----- layout helpers -----
    def _card(self, parent, title, em="", collapsed=False):
        box = tk.Frame(parent, bg=C["card"], highlightbackground=C["line"], highlightthickness=1)
        box.pack(fill="x", padx=16, pady=(0, 12))
        head = tk.Frame(box, bg=C["card"], cursor="hand2")
        head.pack(fill="x", padx=12, pady=10)
        arrow = tk.StringVar(value="▸" if collapsed else "▾")
        tk.Label(head, textvariable=arrow, bg=C["card"], fg=C["muted"], font=FB, width=2).pack(side="left")
        tk.Label(head, text=title, bg=C["card"], fg=C["ink"], font=FB).pack(side="left")
        if em:
            tk.Label(head, text=em, bg=C["card"], fg=C["accent"], font=FS).pack(side="left", padx=8)
        tk.Label(head, text="点击展开/收起", bg=C["card"], fg="#94a3b8", font=(_UI_FONT, 10 if sys.platform == "darwin" else 8)).pack(side="right")
        inner = tk.Frame(box, bg=C["card"])
        if not collapsed:
            inner.pack(fill="x", padx=16, pady=(0, 14))

        def toggle(_e=None):
            if inner.winfo_ismapped():
                inner.pack_forget()
                arrow.set("▸")
            else:
                inner.pack(fill="x", padx=16, pady=(0, 14))
                arrow.set("▾")

        for w in (head, *head.winfo_children()):
            w.bind("<Button-1>", toggle)
        return inner

    def _note(self, parent, text):
        tk.Label(parent, text=text, bg=C["card"], fg=C["muted"], font=FS, wraplength=820, justify="left").pack(anchor="w", pady=(0, 8))

    def _input(self, parent, font=MONO, **kw) -> tk.Entry:
        opts = dict(
            font=font,
            relief="flat",
            bd=1,
            bg="#f8fafc",
            fg=C["ink"],
            insertbackground=C["ink"],
            selectbackground="#bfdbfe",
            selectforeground=C["ink"],
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor=C["accent"],
        )
        opts.update(kw)
        return tk.Entry(parent, **opts)

    def _text_input(self, parent, **kw) -> tk.Text:
        opts = dict(
            font=F,
            relief="flat",
            bd=1,
            bg="#f8fafc",
            fg=C["ink"],
            insertbackground=C["ink"],
            selectbackground="#bfdbfe",
            selectforeground=C["ink"],
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor=C["accent"],
        )
        opts.update(kw)
        return tk.Text(parent, **opts)

    def _radio(self, parent, text, variable, value, command=None, font=FS) -> tk.Radiobutton:
        return tk.Radiobutton(
            parent,
            text=text,
            variable=variable,
            value=value,
            command=command,
            bg=C["card"],
            fg=C["ink"],
            activebackground=C["card"],
            activeforeground=C["ink"],
            selectcolor="#ffffff",
            highlightthickness=0,
            bd=0,
            font=font,
        )

    def _entry(self, parent, label, var, show=None, width=None):
        wrap = tk.Frame(parent, bg=C["card"])
        wrap.pack(fill="x", pady=4)
        tk.Label(wrap, text=label, bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        e = self._input(wrap, font=F, textvariable=var, show=show or "")
        e.pack(fill="x", ipady=5, pady=(3, 0))
        return e

    def _check(self, parent, text, var):
        cb = tk.Checkbutton(
            parent,
            text=text,
            variable=var,
            bg=C["card"],
            fg=C["ink"],
            font=F,
            activebackground=C["card"],
            activeforeground=C["ink"],
            selectcolor="#ffffff",
            highlightthickness=0,
            bd=0,
            anchor="w",
        )
        cb.pack(anchor="w", pady=2)
        return cb

    def _row3(self, parent):
        f = tk.Frame(parent, bg=C["card"])
        f.pack(fill="x", pady=4)
        f.columnconfigure((0, 1, 2), weight=1)
        return f

    def _cell(self, grid, col, label, var, width=14):
        box = tk.Frame(grid, bg=C["card"])
        box.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0))
        tk.Label(box, text=label, bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        self._input(box, font=MONO, textvariable=var).pack(fill="x", ipady=5, pady=(3, 0))
        return box

    def _scroll_tab(self, parent):
        wrap = tk.Frame(parent, bg=C["paper"])
        canvas = tk.Canvas(wrap, bg=C["paper"], highlightthickness=0)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=C["paper"])
        pending = [None]
        def _on_inner(_e=None):
            if pending[0] is not None:
                return
            def update_region():
                pending[0] = None
                if canvas.winfo_exists():
                    region = canvas.bbox("all")
                    if region != getattr(canvas, "_last_region", None):
                        canvas.configure(scrollregion=region)
                        canvas._last_region = region
            pending[0] = self.after_idle(update_region)

        inner.bind("<Configure>", _on_inner)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        def wheel(e):
            step = _scroll_step(e.delta)
            if step:
                canvas.yview_scroll(step, "units")
            return "break"

        canvas.bind("<MouseWheel>", wheel)
        inner.bind("<MouseWheel>", wheel)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        wrap._canvas = canvas
        wrap._inner = inner
        wrap._on_inner = _on_inner
        self._scroll_pages.append(wrap)
        return wrap, inner

    def _wheel_page(self, event):
        """Scroll forms even when the pointer is over a label or entry."""
        widget = event.widget
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Combobox)):
            return
        while widget is not None:
            if any(widget is page._inner for page in self._scroll_pages):
                page = next(page for page in self._scroll_pages if page._inner is widget)
                step = -1 if event.delta > 0 else 1
                page._canvas.yview_scroll(step * max(1, abs(event.delta) // 120), "units")
                return "break"
            widget = getattr(widget, "master", None)

    def _suspend_layout(self) -> None:
        for wrap in self._scroll_pages:
            try:
                wrap._inner.unbind("<Configure>")
            except Exception:
                pass

    def _resume_layout(self) -> None:
        for wrap in self._scroll_pages:
            try:
                wrap._inner.bind("<Configure>", wrap._on_inner)
                wrap._canvas.configure(scrollregion=wrap._canvas.bbox("all"))
            except Exception:
                pass

    def _candidate_asset_dirs(self) -> list[Path]:
        exe_dir = Path(sys.executable).resolve().parent
        dirs = [
            RESOURCE_DIR,
            SCRIPT_DIR,
            Path(__file__).resolve().parent,
            exe_dir,
            exe_dir / "_internal",
            exe_dir.parent / "Resources",
            exe_dir.parent / "Frameworks",
        ]
        out: list[Path] = []
        for d in dirs:
            try:
                rp = d.resolve()
                if rp not in out:
                    out.append(rp)
            except Exception:
                if d not in out:
                    out.append(d)
        return out

    def _apply_window_icon(self) -> None:
        import base64
        import tempfile

        asset_dirs = self._candidate_asset_dirs()
        if self._logo is None:
            for base in asset_dirs:
                for rel in (Path("web") / "static" / "logo.png", Path("logo.png")):
                    p = base / rel
                    if p.is_file():
                        try:
                            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                            self._logo_full = tk.PhotoImage(data=b64)
                            self._logo = self._logo_full.subsample(18, 18)
                            break
                        except Exception:
                            try:
                                self._logo_full = tk.PhotoImage(file=str(p))
                                self._logo = self._logo_full.subsample(18, 18)
                                break
                            except Exception:
                                pass
                if self._logo is not None:
                    break
        if self._logo_full is not None and self._logo is not None:
            try:
                self.iconphoto(True, self._logo_full, self._logo)
            except Exception:
                pass
        for base in asset_dirs:
            ico = base / "logo.ico"
            if ico.is_file():
                if sys.platform == "win32":
                    applied = False
                    for target in (str(ico),):
                        try:
                            self.iconbitmap(default=target)
                            self.iconbitmap(target)
                            applied = True
                        except Exception:
                            pass
                    if not applied or not str(ico).isascii():
                        try:
                            tmp_ico = Path(tempfile.gettempdir()) / "sheets_post_filter_logo.ico"
                            if not tmp_ico.exists() or tmp_ico.stat().st_size != ico.stat().st_size:
                                tmp_ico.write_bytes(ico.read_bytes())
                            self.iconbitmap(default=str(tmp_ico))
                            self.iconbitmap(str(tmp_ico))
                        except Exception:
                            pass
                break

    # ----- build -----
    def _build(self) -> None:
        self._build_header()
        self._build_tabs()
        self._build_log()

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=C["head"])
        head.pack(fill="x")
        left = tk.Frame(head, bg=C["head"])
        left.pack(side="left", padx=18, pady=8)
        if self._logo is None:
            self._apply_window_icon()
        if self._logo is not None:
            tk.Label(left, image=self._logo, bg=C["head"], bd=0, highlightthickness=0).pack(side="left", padx=(0, 12))
        brand = tk.Frame(left, bg=C["head"])
        brand.pack(side="left")
        tk.Label(brand, text="数据汇总工具", fg=C["cream"], bg=C["head"], font=FH).pack(anchor="w")
        tk.Label(
            brand,
            text="选择配置 · 调整参数 · 开始执行",
            fg="#cbd5e1",
            bg=C["head"],
            font=FS,
        ).pack(anchor="w")
        version_row = tk.Frame(brand, bg=C["head"])
        version_row.pack(anchor="w", pady=(5, 0))
        tk.Label(
            version_row,
            text=f"版本 {APP_VERSION}",
            fg="#cbd5e1",
            bg=C["head"],
            font=(_UI_FONT, 10 if sys.platform == "darwin" else 8),
        ).pack(side="left")
        self.btn_update = StyleBtn(
            version_row,
            "head",
            text="检查更新",
            command=self._check_updates,
            bg="#274b79",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            highlightbackground="#5278a8",
            padx=10,
            pady=3,
        )
        self.btn_update.pack(side="left", padx=8)
        self.btn_settings = StyleBtn(
            version_row,
            "head",
            text="设置",
            command=self._open_settings,
            bg="#274b79",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            highlightbackground="#5278a8",
            padx=10,
            pady=3,
        )
        self.btn_settings.pack(side="left")
        StyleBtn(
            version_row,
            "head",
            text="配置导入 / 导出",
            command=self._open_config_transfer,
            bg="#274b79",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            highlightbackground="#5278a8",
            padx=10,
            pady=3,
        ).pack(side="left", padx=8)

        self.status = tk.StringVar(value="待命")
        tk.Label(
            self,
            textvariable=self.status,
            bg=C["paper"],
            fg=C["head"],
            font=FB,
            wraplength=1100,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(10, 0))

        body = tk.Frame(self, bg=C["paper"])
        body.pack(fill="both", expand=True, pady=(6, 0))
        self.sidebar = tk.Frame(body, bg=C["sa"], width=220 if sys.platform == "darwin" else 206)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        side_head = tk.Frame(self.sidebar, bg=C["sa"])
        side_head.pack(fill="x", padx=12, pady=(14, 8))
        tk.Label(side_head, text="配置菜单", bg=C["sa"], fg=C["cream"], font=FB).pack(side="left")
        StyleBtn(
            side_head,
            "primary",
            text="＋",
            command=self._add_menu,
            highlightbackground="#60a5fa",
            padx=10,
            pady=3,
        ).pack(side="right")
        side_actions = tk.Frame(self.sidebar, bg=C["sa"])
        side_actions.pack(side="bottom", fill="x", padx=8, pady=(6, 12))
        StyleBtn(
            side_actions, "head", text="修改名称", command=self._rename_menu,
            bg="#243b5a", fg="#ffffff", activebackground="#1d4ed8", activeforeground="#ffffff",
            highlightbackground="#3b5984", padx=10, pady=5,
        ).pack(side="left")
        StyleBtn(
            side_actions, "head", text="删除", command=self._delete_menu,
            bg="#243b5a", fg="#fecdd3", activebackground="#e11d48", activeforeground="#ffffff",
            highlightbackground="#3b5984", padx=10, pady=5,
        ).pack(side="right")
        menu_wrap = tk.Frame(self.sidebar, bg=C["sa"])
        menu_wrap.pack(fill="both", expand=True, padx=8)
        self._menu_canvas = tk.Canvas(menu_wrap, bg=C["sa"], highlightthickness=0, bd=0)
        menu_bar = ttk.Scrollbar(menu_wrap, orient="vertical", command=self._menu_canvas.yview)
        self.menu_box = tk.Frame(self._menu_canvas, bg=C["sa"])
        self._menu_canvas_win = self._menu_canvas.create_window((0, 0), window=self.menu_box, anchor="nw")
        self._menu_canvas.configure(yscrollcommand=menu_bar.set)
        self.menu_box.bind("<Configure>", lambda _e: self._menu_canvas.configure(scrollregion=self._menu_canvas.bbox("all")))
        self._menu_canvas.bind("<Configure>", lambda e: self._menu_canvas.itemconfigure(self._menu_canvas_win, width=e.width))

        def _menu_wheel(event):
            step = _scroll_step(event.delta)
            if step:
                self._menu_canvas.yview_scroll(step, "units")
            return "break"

        self._menu_canvas.bind("<MouseWheel>", _menu_wheel)
        self.menu_box.bind("<MouseWheel>", _menu_wheel)
        self._menu_canvas.pack(side="left", fill="both", expand=True)
        menu_bar.pack(side="right", fill="y")

        right = tk.Frame(body, bg=C["paper"])
        right.pack(side="left", fill="both", expand=True)
        actions = tk.Frame(right, bg=C["paper"])
        actions.pack(fill="x", padx=16, pady=(2, 8))
        self.btn_run = StyleBtn(actions, "primary", text="开始汇总", command=self._run_now)
        self.btn_pub = StyleBtn(actions, "head", text="发布图库", command=self._publish)
        self.btn_video = StyleBtn(actions, "head", text="提取视频时长", command=self._run_video)
        self.btn_posts = StyleBtn(actions, "primary", text="开始贴文汇总", command=self._run_posts)
        self.btn_wm = StyleBtn(actions, "primary", text="开始多工作表汇总", command=self._run_worksheet_merge)
        self.btn_wm_publish = StyleBtn(actions, "head", text="发布图库", command=self._publish_worksheet_gallery)
        self.btn_stop = StyleBtn(actions, "ghost", text="停止当前", command=self._stop_current)
        self.btn_run_selected = StyleBtn(actions, "head", text="执行所选", command=self._run_selected)
        self.btn_stop_all = StyleBtn(actions, "ghost", text="全部停止", command=self._stop_all)
        self.btn_save = StyleBtn(actions, "ghost", text="保存配置", command=self._save)

        self.pages = tk.Frame(right, bg=C["paper"])
        self.pages.pack(fill="both", expand=True)
        self.filter_page, self.filter_inner = self._scroll_tab(self.pages)
        self.video_page, self.video_inner = self._scroll_tab(self.pages)
        self.filter_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.posts_page, self.posts_inner = self._scroll_tab(self.pages)
        self.posts_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.video_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.wm_page, self.wm_inner = self._scroll_tab(self.pages)
        self.wm_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._build_filter(self.filter_inner)
        self._build_posts(self.posts_inner)
        self._build_video(self.video_inner)
        self._build_worksheet_merge(self.wm_inner)

    def _build_tabs(self) -> None:
        pass

    def _show_tab(self, name: str) -> None:
        same = getattr(self, "_tab", None) == name
        self._tab = name
        if same:
            if name in ("video", "custom"):
                self._apply_vd_mode(name)
            return
        mapping = {
            "filter": self.filter_page,
            "posts": self.posts_page,
            "video": self.video_page,
            "worksheet_merge": self.wm_page,
        }
        mapping.get(name, self.filter_page).lift()
        for btn in (self.btn_run, self.btn_pub, self.btn_video, self.btn_posts, self.btn_wm, self.btn_wm_publish, self.btn_save):
            btn.pack_forget()
        if name == "filter":
            self.btn_run.pack(side="left", padx=(0, 8))
            self.btn_pub.pack(side="left", padx=(0, 8))
        elif name == "posts":
            self.btn_posts.pack(side="left", padx=(0, 8))
        elif name == "worksheet_merge":
            self.btn_wm.pack(side="left", padx=(0, 8))
            self.btn_wm_publish.pack(side="left", padx=(0, 8))
        elif name in ("video", "custom"):
            self.btn_video.configure(text="提取视频时长" if name == "video" else "开始分类汇总")
            self.btn_video.pack(side="left", padx=(0, 8))
            self._apply_vd_mode(name)
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.btn_run_selected.pack(side="left", padx=(0, 8))
        self.btn_stop_all.pack(side="left", padx=(0, 8))
        self.btn_save.pack(side="left")

    def _init_menus(self) -> None:
        base = asdict(self.cfg)
        legacy = not self.cfg.ui_menus
        self._menus, self.cfg.ui_archived_menus = partition_menus(self.cfg.ui_menus, self.cfg.ui_archived_menus)
        for key, label, _description in TEMPLATES:
            if any(item.get("template") == key for item in self._menus):
                continue
            # Preserve the original single-config installation once; never seed
            # unrelated templates with its private data.
            settings = copy.deepcopy(base) if legacy and key == "filter" else new_template_settings(key)
            settings.pop("ui_menus", None)
            settings.pop("ui_archived_menus", None)
            if key == "posts":
                settings.update(self._posts_default_settings())
            if key == "video":
                settings["vd_write_log"] = True
            self._menus.append({"id": f"{key}-default", "name": label, "template": key, "settings": settings})
        for item in self._menus:
            item.setdefault("id", f"{item['template']}-{id(item)}")
            item.setdefault("name", TEMPLATE_NAMES[item["template"]])
            item.setdefault("settings", {})
            if item["template"] == "video":
                item["settings"]["vd_write_log"] = True
        wanted = self.cfg.ui_active_menu or ""
        active = next((item["id"] for item in self._menus if item["id"] == wanted), self._menus[0]["id"])
        self.cfg.ui_menus = copy.deepcopy(self._menus)
        self._render_menu_buttons()
        self._select_menu(active, initial=True)

    def _render_menu_buttons(self) -> None:
        for child in self.menu_box.winfo_children():
            child.destroy()
        self._menu_buttons = {}
        if not hasattr(self, "_menu_checks"):
            self._menu_checks = {}
        for item in self._menus:
            wrap = tk.Frame(self.menu_box, bg=C["sa"])
            wrap.pack(fill="x", pady=2)
            var = self._menu_checks.get(item["id"])
            if var is None:
                var = tk.BooleanVar(value=True)
                self._menu_checks[item["id"]] = var
            tk.Checkbutton(
                wrap,
                variable=var,
                bg=C["sa"],
                activebackground=C["sa"],
                selectcolor=C["head"],
                fg=C["cream"],
                activeforeground=C["cream"],
                relief="flat",
                bd=0,
                highlightthickness=0,
            ).pack(side="left", padx=(2, 4))
            labels = TEMPLATE_NAMES
            text = f"{item['name']}\n  {labels.get(item['template'], '')}"
            is_active = item["id"] == self._active_menu_id
            btn = StyleBtn(
                wrap,
                "head",
                text=text,
                command=lambda menu_id=item["id"]: self._select_menu(menu_id),
                anchor="w",
                justify="left",
                bg=C["accent"] if is_active else "#1e3250",
                fg="#ffffff" if is_active else C["cream"],
                activebackground="#1d4ed8" if is_active else "#274b79",
                activeforeground="#ffffff",
                highlightthickness=1,
                highlightbackground="#60a5fa" if is_active else "#294366",
                padx=10,
                pady=8,
                font=F,
            )
            btn.pack(side="left", fill="x", expand=True)
            btn.bind("<Double-Button-1>", lambda _event, menu_id=item["id"]: self._rename_menu(menu_id), add="+")
            btn.bind(
                "<MouseWheel>",
                lambda e: (self._menu_canvas.yview_scroll(_scroll_step(e.delta), "units") if _scroll_step(e.delta) else None) or "break",
                add="+",
            )
            self._menu_buttons[item["id"]] = btn

    def _store_current_menu_settings(self) -> None:
        current = next((item for item in self._menus if item["id"] == self._active_menu_id), None)
        if not current:
            return
        settings = current.setdefault("settings", {})
        settings.update(self._settings_slice(current.get("template") or "filter"))
        self._page_settings[current["template"]] = copy.deepcopy(settings)
        page = {"filter": self.filter_page, "posts": self.posts_page,
                "video": self.video_page, "worksheet_merge": self.wm_page}.get(current["template"])
        if page:
            self._menu_scroll[current["id"]] = page._canvas.yview()[0]

    def _select_menu(self, menu_id: str, initial: bool = False) -> None:
        if menu_id == self._active_menu_id and not initial:
            self._pending_select = None
            if self._selection_after is not None:
                self.after_cancel(self._selection_after)
                self._selection_after = None
            return
        item = next((entry for entry in self._menus if entry["id"] == menu_id), None)
        if not item:
            return
        self._pending_select = item
        if self._selection_after is not None:
            self.after_cancel(self._selection_after)
            self._selection_after = None
        if initial:
            self._finish_select()
            return
        # Coalesce rapid clicks; the active ID always refers to visible fields.
        self._selection_after = self.after_idle(self._finish_select)

    def _finish_select(self, item: dict | None = None) -> None:
        self._selection_after = None
        try:
            target = self._pending_select or item
            self._pending_select = None
            if not target or target.get("id") == self._active_menu_id:
                return
            self._store_current_menu_settings()
            self._selecting_menu = True
            self._active_menu_id = target["id"]
            self._apply_selected_menu(target)
            self._highlight_menu_buttons()
            self.status.set(f"当前配置：{target['name']}")
        finally:
            self._selecting_menu = False

    def _apply_selected_menu(self, item: dict) -> None:
        template = item.get("template") or "filter"
        settings = item.get("settings") or {}
        if self._page_settings.get(template) != settings:
            self._suspend_layout()
            try:
                self._apply_settings_slice(settings, template)
                self._page_settings[template] = copy.deepcopy(settings)
            finally:
                self._resume_layout()
        self._show_tab(template)
        page = {"filter": self.filter_page, "posts": self.posts_page,
                "video": self.video_page, "worksheet_merge": self.wm_page}[template]
        page._canvas.yview_moveto(self._menu_scroll.get(item["id"], 0))
        job = job_snapshot(item.get("id") or "")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        lines = job.get("logs") or []
        self.log.insert("end", "".join(f"[{line.get('t','')}] {line.get('msg','')}\n" for line in lines[-600:]))
        self.log.configure(state="disabled")
        self._log_n = len(lines)

    def _highlight_menu_buttons(self) -> None:
        for menu_id, btn in self._menu_buttons.items():
            try:
                active = menu_id == self._active_menu_id
                btn.configure(
                    bg=C["accent"] if active else "#1e3250",
                    fg="#ffffff" if active else C["cream"],
                    activebackground="#1d4ed8" if active else "#274b79",
                    highlightbackground="#60a5fa" if active else "#294366",
                )
            except Exception:
                pass

    def _rename_menu(self, menu_id: str | None = None) -> None:
        menu_id = menu_id or self._active_menu_id
        item = next((entry for entry in self._menus if entry["id"] == menu_id), None)
        if not item:
            return
        name = simpledialog.askstring("修改菜单名称", "新名称：", initialvalue=item["name"], parent=self)
        if name and name.strip():
            item["name"] = name.strip()
            labels = TEMPLATE_NAMES
            btn = self._menu_buttons.get(menu_id)
            if btn:
                btn.configure(text=f"{item['name']}\n  {labels.get(item['template'], '')}")
            else:
                self._render_menu_buttons()

    def _pick_template(self) -> str:
        win = tk.Toplevel(self)
        win.title("新增配置菜单")
        win.transient(self)
        win.configure(bg=C["paper"])
        win.resizable(False, False)
        tk.Label(win, text="选择模板类型", bg=C["paper"], fg=C["ink"], font=FB).pack(anchor="w", padx=18, pady=(16, 8))
        chosen: dict[str, str] = {}
        options = TEMPLATES

        def pick(key: str) -> None:
            chosen["template"] = key
            win.destroy()

        for key, title, desc in options:
            btn = StyleBtn(
                win,
                "ghost",
                text=f"{title}\n{desc}",
                command=lambda k=key: pick(k),
                anchor="w",
                justify="left",
                bg=C["card"],
                fg=C["ink"],
                activebackground="#dbeafe",
                activeforeground=C["ink"],
                padx=14,
                pady=10,
                font=F,
            )
            btn.pack(fill="x", padx=18, pady=4)
        StyleBtn(win, "ghost", text="取消", command=win.destroy).pack(anchor="e", padx=18, pady=(8, 16))
        win.update_idletasks()
        win.geometry(f"+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 80}")
        win.grab_set()
        self.wait_window(win)
        return chosen.get("template") or ""

    def _add_menu(self) -> None:
        template = self._pick_template()
        if not template:
            return
        labels = TEMPLATE_NAMES
        name = simpledialog.askstring("新增配置菜单", "菜单名称：", initialvalue=f"新{labels.get(template, template)}", parent=self)
        if not name or not name.strip():
            return
        settings = new_template_settings(template)
        menu_id = f"{template}-{int(datetime.now().timestamp() * 1000)}"
        self._menus.append({"id": menu_id, "name": name.strip(), "template": template, "settings": settings})
        self._render_menu_buttons()
        self._select_menu(menu_id)

    def _delete_menu(self) -> None:
        if len(self._menus) <= 1:
            messagebox.showwarning("数据汇总工具", "至少保留一个配置菜单")
            return
        item = next((entry for entry in self._menus if entry["id"] == self._active_menu_id), None)
        if not item or not messagebox.askyesno("删除配置", f"确定删除“{item['name']}”？"):
            return
        self._menus = [entry for entry in self._menus if entry["id"] != self._active_menu_id]
        self._active_menu_id = ""
        self._render_menu_buttons()
        self._select_menu(self._menus[0]["id"], initial=True)
        self._sync_schedulers()

    def _src_table(self, parent, headers, add_text, rows_attr, name_ph):
        head = tk.Frame(parent, bg="#e8eef7")
        head.pack(fill="x")
        tk.Label(head, text=headers[0], bg="#e8eef7", font=FS, fg=C["muted"], width=16, anchor="w").pack(side="left", padx=6, pady=4)
        tk.Label(head, text=headers[1], bg="#e8eef7", font=FS, fg=C["muted"], anchor="w").pack(side="left", fill="x", expand=True)
        box = tk.Frame(parent, bg=C["card"])
        box.pack(fill="x")
        hint = tk.Frame(parent, bg=C["card"])
        hint.pack(fill="x", pady=6)
        count = tk.Label(hint, text="0 个链接", bg=C["card"], fg=C["muted"], font=FS)
        count.pack(side="left")
        StyleBtn(hint, "ghost", text=add_text, command=lambda: self._add_src_row(box, rows_attr, name_ph, count)).pack(side="right")
        StyleBtn(hint, "ghost", text="清空", command=lambda: self._clear_src(box, rows_attr, name_ph, count)).pack(side="right", padx=6)
        return box, count, name_ph

    def _add_src_row(self, box, rows_attr, name_ph, count, name="", url=""):
        row = tk.Frame(box, bg=C["card"])
        row.pack(fill="x", pady=3)
        n = self._input(row, font=MONO, width=16)
        n.insert(0, name)
        n.pack(side="left", ipady=4)
        u = self._input(row, font=MONO)
        u.insert(0, url)
        u.pack(side="left", fill="x", expand=True, padx=6, ipady=4)
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_src_row(row, box, rows_attr, name_ph, count)).pack(side="left")
        row._name = n
        row._url = u
        getattr(self, rows_attr).append(row)
        self._upd_src_count(rows_attr, count)

    def _del_src_row(self, row, box, rows_attr, name_ph, count):
        lst = getattr(self, rows_attr)
        if row in lst:
            lst.remove(row)
        row.destroy()
        if not lst:
            self._add_src_row(box, rows_attr, name_ph, count)
        self._upd_src_count(rows_attr, count)

    def _clear_src(self, box, rows_attr, name_ph, count):
        for r in list(getattr(self, rows_attr)):
            r.destroy()
        setattr(self, rows_attr, [])
        self._add_src_row(box, rows_attr, name_ph, count)
        self._add_src_row(box, rows_attr, name_ph, count)

    def _upd_src_count(self, rows_attr, count):
        n = sum(1 for r in getattr(self, rows_attr) if r._url.get().strip())
        count.configure(text=f"{n} 个链接")

    def _read_src(self, rows_attr, align=False):
        out = []
        for r in getattr(self, rows_attr):
            label = r._name.get().strip()
            url = r._url.get().strip()
            if not url:
                continue
            out.append({"sheet": label, "url": url} if align else {"name": label, "url": url})
        return out

    def _set_src(self, box, rows_attr, name_ph, count, items, align=False):
        parsed: list[tuple[str, str]] = []
        for item in items or []:
            if isinstance(item, str):
                parsed.append(("", item))
            elif isinstance(item, dict):
                parsed.append((str(item.get("sheet") or item.get("name") or ""), str(item.get("url") or "")))
        if not parsed:
            parsed = [("", ""), ("", "")]
        rows = getattr(self, rows_attr)
        while len(rows) > len(parsed):
            row = rows.pop()
            row.destroy()
        for index, (name, url) in enumerate(parsed):
            if index < len(rows):
                self._fill_entry(rows[index]._name, name)
                self._fill_entry(rows[index]._url, url)
            else:
                self._add_src_row(box, rows_attr, name_ph, count, name, url)
        self._upd_src_count(rows_attr, count)

    def _fill_entry(self, entry: tk.Entry, value: str) -> None:
        current = entry.get()
        if current == value:
            return
        entry.delete(0, "end")
        if value:
            entry.insert(0, value)

    def _set_entry_rows(self, rows: list, add_fn, values, empty: int = 1) -> None:
        cleaned = [str(item).strip() for item in (values or []) if str(item).strip()]
        target = cleaned or ([""] * empty)
        while len(rows) > len(target):
            row = rows.pop()
            row.destroy()
        for index, value in enumerate(target):
            if index < len(rows):
                self._fill_entry(rows[index]._val, value)
            else:
                add_fn(value)

    def _add_vd_type(self, value: str = "", in_total: bool = True, in_item: bool = True, duration_split: bool = False) -> None:
        row = tk.Frame(self.vd_type_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        e = self._input(row, font=MONO)
        e.insert(0, value)
        e.pack(side="left", fill="x", expand=True, ipady=4)
        checks = tk.Frame(row, bg=C["card"])
        checks.pack(side="left", padx=(8, 0))
        var_total = tk.BooleanVar(value=bool(in_total))
        var_item = tk.BooleanVar(value=bool(in_item))
        var_split = tk.BooleanVar(value=bool(duration_split))
        for label, var in (("总计数", var_total), ("逐条计数", var_item), ("按时长分类", var_split)):
            tk.Checkbutton(
                checks,
                text=label,
                variable=var,
                bg=C["card"],
                fg=C["ink"],
                activebackground=C["card"],
                activeforeground=C["ink"],
                selectcolor="#ffffff",
                highlightthickness=0,
                bd=0,
                font=FS,
            ).pack(side="left")
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_vd_type(row)).pack(side="left", padx=(6, 0))
        row._val = e
        row._in_total = var_total
        row._in_item = var_item
        row._duration_split = var_split
        row._checks = checks
        e.bind("<KeyRelease>", lambda _e: self._upd_vd_type_count())
        self._vd_type_rows.append(row)
        self._upd_vd_type_count()
        if getattr(self, "_vd_ui_mode", "") == "custom":
            checks.pack_forget()

    def _del_vd_type(self, row) -> None:
        if row in self._vd_type_rows:
            self._vd_type_rows.remove(row)
        row.destroy()
        if not self._vd_type_rows:
            self._add_vd_type()
        self._upd_vd_type_count()

    def _clear_vd_types(self) -> None:
        for r in list(self._vd_type_rows):
            r.destroy()
        self._vd_type_rows = []
        self._add_vd_type()
        self._add_vd_type()
        self._upd_vd_type_count()

    def _upd_vd_type_count(self) -> None:
        n = sum(1 for r in self._vd_type_rows if r._val.get().strip())
        self.vd_type_count.configure(text=f"{n} 个分类")

    def _read_vd_types(self) -> list:
        out = []
        seen: set[str] = set()
        for r in self._vd_type_rows:
            t = r._val.get().strip()
            if t and t not in seen:
                seen.add(t)
                out.append(
                    {
                        "name": t,
                        "in_total": bool(r._in_total.get()) if hasattr(r, "_in_total") else True,
                        "in_item": bool(r._in_item.get()) if hasattr(r, "_in_item") else True,
                        "duration_split": bool(r._duration_split.get()) if hasattr(r, "_duration_split") else False,
                    }
                )
        return out

    @staticmethod
    def _default_vd_duration_rules() -> list[dict]:
        return [
            {"name": "60秒内", "min_seconds": "", "max_seconds": "60"},
            {"name": "61-180秒", "min_seconds": "60", "max_seconds": "180"},
            {"name": "181秒以上", "min_seconds": "180", "max_seconds": ""},
        ]

    def _add_vd_duration_rule(self, item=None) -> None:
        data = item if isinstance(item, dict) else {}
        row = tk.Frame(self.vd_duration_rule_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        name = self._input(row, font=MONO)
        lower = self._input(row, font=MONO, width=14)
        upper = self._input(row, font=MONO, width=14)
        name.insert(0, str(data.get("name") or ""))
        lower.insert(0, "" if data.get("min_seconds") in (None, "") else str(data.get("min_seconds")))
        upper.insert(0, "" if data.get("max_seconds") in (None, "") else str(data.get("max_seconds")))
        name.pack(side="left", fill="x", expand=True, ipady=4)
        lower.pack(side="left", padx=(8, 0), ipady=4)
        upper.pack(side="left", padx=(8, 0), ipady=4)
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_vd_duration_rule(row)).pack(side="left", padx=(6, 0))
        row._name = name
        row._min = lower
        row._max = upper
        self._vd_duration_rule_rows.append(row)
        self._upd_vd_duration_rule_count()

    def _del_vd_duration_rule(self, row) -> None:
        if row in self._vd_duration_rule_rows:
            self._vd_duration_rule_rows.remove(row)
        row.destroy()
        if not self._vd_duration_rule_rows:
            self._add_vd_duration_rule()
        self._upd_vd_duration_rule_count()

    def _upd_vd_duration_rule_count(self) -> None:
        if hasattr(self, "vd_duration_rule_count"):
            count = sum(1 for row in self._vd_duration_rule_rows if row._name.get().strip())
            self.vd_duration_rule_count.configure(text=f"{count} 个时长区间")

    def _read_vd_duration_rules(self) -> list[dict]:
        rules: list[dict] = []
        for row in self._vd_duration_rule_rows:
            name = row._name.get().strip()
            if not name:
                continue
            rules.append(
                {
                    "name": name,
                    "min_seconds": row._min.get().strip() or None,
                    "max_seconds": row._max.get().strip() or None,
                }
            )
        return rules or self._default_vd_duration_rules()

    def _set_vd_duration_rules(self, items) -> None:
        for row in list(self._vd_duration_rule_rows):
            row.destroy()
        self._vd_duration_rule_rows = []
        rules = [item for item in (items or []) if isinstance(item, dict)] or self._default_vd_duration_rules()
        for item in rules:
            self._add_vd_duration_rule(item)
        self._upd_vd_duration_rule_count()

    def _add_vd_report_cat(self, value: str = "") -> None:
        row = tk.Frame(self.vd_report_cat_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        e = self._input(row, font=MONO)
        e.insert(0, value)
        e.pack(side="left", fill="x", expand=True, ipady=4)
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_vd_report_cat(row)).pack(side="left", padx=(6, 0))
        row._val = e
        e.bind("<KeyRelease>", lambda _e: self._upd_vd_report_cat_count())
        self._vd_report_rows.append(row)
        self._upd_vd_report_cat_count()

    def _del_vd_report_cat(self, row) -> None:
        if row in self._vd_report_rows:
            self._vd_report_rows.remove(row)
        row.destroy()
        if not self._vd_report_rows:
            self._add_vd_report_cat()
        self._upd_vd_report_cat_count()

    def _upd_vd_report_cat_count(self) -> None:
        n = sum(1 for r in self._vd_report_rows if r._val.get().strip())
        if hasattr(self, "vd_report_cat_count"):
            self.vd_report_cat_count.configure(text=f"{n} 个额外列")

    def _read_vd_report_cats(self) -> list[str]:
        out = []
        seen: set[str] = set()
        for r in self._vd_report_rows:
            t = r._val.get().strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _set_vd_report_cats(self, items) -> None:
        self._set_entry_rows(self._vd_report_rows, self._add_vd_report_cat, items, empty=1)
        self._upd_vd_report_cat_count()

    def _add_vd_exclude(self, value: str = "") -> None:
        row = tk.Frame(self.vd_exclude_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        e = self._input(row, font=MONO)
        e.insert(0, value)
        e.pack(side="left", fill="x", expand=True, ipady=4)
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_vd_exclude(row)).pack(side="left", padx=(6, 0))
        row._val = e
        e.bind("<KeyRelease>", lambda _e: self._upd_vd_exclude_count())
        self._vd_exclude_rows.append(row)
        self._upd_vd_exclude_count()

    def _del_vd_exclude(self, row) -> None:
        if row in self._vd_exclude_rows:
            self._vd_exclude_rows.remove(row)
        row.destroy()
        if not self._vd_exclude_rows:
            self._add_vd_exclude()
        self._upd_vd_exclude_count()

    def _upd_vd_exclude_count(self) -> None:
        n = sum(1 for r in self._vd_exclude_rows if r._val.get().strip())
        if hasattr(self, "vd_exclude_count"):
            self.vd_exclude_count.configure(text=f"{n} 个排除")

    def _read_vd_exclude_types(self) -> list[str]:
        out = []
        seen: set[str] = set()
        for r in self._vd_exclude_rows:
            t = r._val.get().strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _set_vd_exclude_types(self, items) -> None:
        self._set_entry_rows(self._vd_exclude_rows, self._add_vd_exclude, items, empty=1)
        self._upd_vd_exclude_count()

    def _apply_vd_category_mode(self) -> None:
        other_only = "不统计" in (self.var_vd_category_mode.get() or "")
        if other_only:
            self.vd_exclude_wrap.pack_forget()
            self.vd_type_note.configure(
                text="这些分类不统计（同样支持包含匹配，如 wsp）。没填分类的、以及其他分类，全部计入下面的「其余」。"
            )
        else:
            if not self.vd_exclude_wrap.winfo_ismapped():
                self.vd_exclude_wrap.pack(fill="x", after=self.vd_mode_row)
            self.vd_type_note.configure(
                text="单独成列的分类：每个分类一列。填 wsp 会匹配所有包含 wsp 的类型；可用 * 通配符，例如 口播*。没填分类的算进「其余」。"
            )

    def _apply_vd_mode(self, name: str) -> None:
        if self._vd_ui_mode == name:
            return
        self._vd_ui_mode = name
        custom = name == "custom"
        if custom:
            self.vd_video_filter_row.pack_forget()
            self.vd_custom_box.pack(fill="x", pady=(8, 0))
            self.vd_link_cell.grid_remove()
            self.vd_log_cell.grid_remove()
            self.vd_video_opts.pack_forget()
            if hasattr(self, "vd_duration_rule_wrap"):
                self.vd_duration_rule_wrap.pack_forget()
            if hasattr(self, "vd_report_cat_wrap"):
                self.vd_report_cat_wrap.pack_forget()
            if hasattr(self, "vd_force_refresh_btn"):
                self.vd_force_refresh_btn.pack_forget()
            if hasattr(self, "vd_wildcard_note"):
                self.vd_wildcard_note.pack(anchor="w", pady=(0, 4))
            if hasattr(self, "vd_type_head"):
                self.vd_type_head.pack_forget()
            for row in self._vd_type_rows:
                if hasattr(row, "_checks"):
                    row._checks.pack_forget()
            self._apply_vd_category_mode()
            self.vd_dest_note.configure(text="写入目标表：按制作人 × 日期 × 分类统计条数。不写日志、不提取时长、没有 30 秒算法。")
            self.vd_src_note.configure(text="读取源表的日期、制作人和类型，按下面的分类规则统计每天每个人每个分类有多少条。不读视频链接、不查时长。")
            self.vd_col_note.configure(text="列用字母。默认：A=日期，H=制作人，E=类型。")
            self.vd_sched_note.configure(text="到点后按分类规则重新统计并写入数据表。首次启用约 12 秒后执行一轮。")
            self.vd_sched_check.configure(text="启用分类汇总定时")
            self.vd_help_note.configure(
                text="数据表第 1 行是姓名（多分类会合并单元格），第 3 行是分类，第 5 行是每人每类合计，第 8 行起是每天条数。"
                "单独列出的分类各占一列；排除的不算；其余可归入「其他」。源表和写入表都要共享给服务账号。"
            )
        else:
            self.vd_custom_box.pack_forget()
            self.vd_video_filter_row.pack(fill="x", pady=4)
            self.vd_link_cell.grid()
            self.vd_log_cell.grid()
            self.vd_video_opts.pack(fill="x", pady=4)
            if hasattr(self, "vd_duration_rule_wrap"):
                self.vd_duration_rule_wrap.pack(fill="x", pady=(8, 0), after=self.vd_video_opts)
            if hasattr(self, "vd_report_cat_wrap"):
                self.vd_report_cat_wrap.pack(fill="x", pady=(8, 0))
            if hasattr(self, "vd_force_refresh_btn"):
                self.vd_force_refresh_btn.pack(anchor="e", pady=(8, 2))
            if hasattr(self, "vd_wildcard_note"):
                self.vd_wildcard_note.pack_forget()
            if hasattr(self, "vd_type_head") and not self.vd_type_head.winfo_ismapped():
                self.vd_type_head.pack(fill="x", pady=(0, 2), before=self.vd_type_box)
            for row in self._vd_type_rows:
                if hasattr(row, "_checks") and not row._checks.winfo_ismapped():
                    delete_btn = next((child for child in row.winfo_children() if isinstance(child, (tk.Button, StyleBtn)) and str(child.cget("text")) == "删除"), None)
                    if delete_btn is not None:
                        row._checks.pack(side="left", padx=(8, 0), before=delete_btn)
                    else:
                        row._checks.pack(side="left", padx=(8, 0))
            self.vd_type_note.configure(
                text="只跑这些分类（精确匹配）。都会提取时长写入日志。"
                "勾选「总计数」后计入当前的总计数算法；勾选「按时长分类」后，才按下方时间规则拆分统计，并计入逐条计数。未勾选按时长分类的分类，不会进入逐条计数。"
            )
            self.vd_dest_note.configure(
                text="可选择总秒数换算、逐条阶梯计数或按时长区间统计数量。时长区间模式每条视频只计入一个区间；额外分类列仍按视频个数计。"
            )
            self.vd_src_note.configure(
                text="读取源表：A 列日期、B 列视频链接、H 列制作人（列字母可改）。B 列可以是蓝字文件名，程序会读取单元格里的超链接（Drive / YouTube），再写入另一张表的「日志表」和「数据表」。"
            )
            self.vd_col_note.configure(text="列用字母。默认：A=日期，B=视频链接，H=制作人，E=类型。")
            self.vd_sched_note.configure(text="到点后自动读取新链接、追加日志，并重新生成数据表。首次启用约 12 秒后执行一轮。")
            self.vd_sched_check.configure(text="启用视频时长定时")
            self.vd_help_note.configure(
                text="日志表：A 日期、B 链接、C 名字、D 时长(秒)、E 类型、F 备注（未识别原因）。已跑过的链接下次自动跳过。"
                "数据表：未勾选按时长分类的分类显示普通数量；勾选后按时间规则展开子列，并且只有勾选的分类进入逐条计数。"
                "未识别常见原因：不是视频、没有权限、Drive 还没生成时长。源表和 Drive 文件都要共享给服务账号。"
            )

    def _set_vd_types(self, items) -> None:
        for row in list(self._vd_type_rows):
            row.destroy()
        self._vd_type_rows = []
        cleaned = []
        for item in items or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    cleaned.append(
                        (
                            name,
                            bool(item.get("in_total", True)),
                            bool(item.get("in_item", True)),
                            bool(item.get("duration_split", False)),
                        )
                    )
            elif str(item).strip():
                cleaned.append((str(item).strip(), True, True, False))
        if not cleaned:
            self._add_vd_type()
            self._add_vd_type()
            return
        for name, in_total, in_item, duration_split in cleaned:
            self._add_vd_type(name, in_total, in_item, duration_split)

    def _add_vd_extra_col(self, field: str = "分类", column: str = "") -> None:
        row = tk.Frame(self.vd_extra_col_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        name = self._input(row, font=MONO, width=18)
        name.insert(0, field)
        name.pack(side="left", ipady=4)
        col = self._input(row, font=MONO, width=10)
        col.insert(0, column)
        col.pack(side="left", padx=6, ipady=4)
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_vd_extra_col(row)).pack(side="left")
        row._field = name
        row._column = col
        self._vd_extra_col_rows.append(row)

    def _del_vd_extra_col(self, row) -> None:
        if row in self._vd_extra_col_rows:
            self._vd_extra_col_rows.remove(row)
        row.destroy()

    def _read_vd_columns(self) -> list[dict]:
        result = [
            {"field": "日期", "role": "date", "column": self.var_vd_col_date.get().strip().upper()},
            {"field": "名字", "role": "name", "column": self.var_vd_col_name.get().strip().upper()},
            {"field": "类型", "role": "type", "column": self.var_vd_col_type.get().strip().upper()},
        ]
        is_video = next(
            (item.get("template") == "video" for item in self._menus if item.get("id") == self._active_menu_id),
            True,
        )
        if is_video:
            result.insert(1, {"field": "视频链接", "role": "link", "column": self.var_vd_col_link.get().strip().upper()})
        for row in self._vd_extra_col_rows:
            column = row._column.get().strip().upper()
            if column:
                result.append({"field": row._field.get().strip() or "分类", "role": "type", "column": column})
        return [item for item in result if item["column"]]

    def _set_vd_columns(self, items) -> None:
        for row in list(self._vd_extra_col_rows):
            row.destroy()
        self._vd_extra_col_rows = []
        columns = [item for item in (items or []) if isinstance(item, dict)]
        role_vars = {"date": self.var_vd_col_date, "link": self.var_vd_col_link, "name": self.var_vd_col_name}
        type_seen = False
        for item in columns:
            role = str(item.get("role") or "type").lower()
            column = str(item.get("column") or "").upper()
            if role in role_vars:
                role_vars[role].set(column)
            elif role == "type" and not type_seen:
                self.var_vd_col_type.set(column)
                type_seen = True
            elif role == "type":
                self._add_vd_extra_col(str(item.get("field") or "分类"), column)

    def _build_filter(self, p) -> None:
        c1 = self._card(p, "1. 数据源表格链接", "只换链接，列范围相同", collapsed=True)
        self._note(c1, "每个源表都是同一套表头。分组名可选，用来写进汇总结果。")
        box, count, _ph = self._src_table(c1, ["分组（可选）", "表格链接"], "+ 添加数据源", "_src_rows", "例如：分组A")
        self.src_box, self.src_count = box, count
        self.var_add_source_column = tk.BooleanVar(value=True)
        self._check(c1, "把「分组」写到 AC 列（不占用 A 列）", self.var_add_source_column)

        c2 = self._card(p, "2. 日期与点赞过滤")
        self.var_start = tk.StringVar()
        self.var_end = tk.StringVar()
        self.var_likes = tk.StringVar(value="1000")
        g = self._row3(c2)
        self._cell(g, 0, "开始日期", self.var_start)
        self._cell(g, 1, "结束日期", self.var_end)
        self._cell(g, 2, "点赞阈值", self.var_likes)
        self._note(c2, "日期先筛一遍；高亮表再取互动数 ≥ 阈值的行。默认按编号列增量更新。")

        c3 = self._card(p, "3. 写入结果", "两套配置互不影响", collapsed=True)
        self.var_upsert = tk.BooleanVar(value=True)
        self._check(c3, "按编号列更新（已有行只改变化的列，新编号追加；取消则整表覆盖）", self.var_upsert)
        self.var_write_all = tk.BooleanVar(value=True)
        self._check(c3, "全部结果（日期筛选后的所有行）", self.var_write_all)
        self.var_target_url = tk.StringVar()
        self._entry(c3, "表格链接", self.var_target_url)
        g = self._row3(c3)
        self.var_output_sheet = tk.StringVar(value="筛选结果")
        self.var_output_start_row = tk.StringVar(value="1")
        self.var_include_headers = tk.BooleanVar(value=True)
        self._cell(g, 0, "工作表名", self.var_output_sheet)
        self._cell(g, 1, "起始行", self.var_output_start_row)
        box = tk.Frame(g, bg=C["card"])
        box.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        tk.Label(box, text=" ", bg=C["card"]).pack()
        self._check(box, "写入表头", self.var_include_headers)

        tk.Frame(c3, bg=C["line"], height=1).pack(fill="x", pady=10)
        self.var_write_hot = tk.BooleanVar(value=True)
        self._check(c3, "高亮结果（互动数 ≥ 阈值）", self.var_write_hot)
        self.var_hot_target_url = tk.StringVar()
        self._entry(c3, "表格链接（留空 = 与全部结果同一张表）", self.var_hot_target_url)
        g = self._row3(c3)
        self.var_hot_output_sheet = tk.StringVar(value="高亮结果")
        self.var_hot_start_row = tk.StringVar(value="1")
        self.var_hot_include_headers = tk.BooleanVar(value=True)
        self._cell(g, 0, "工作表名", self.var_hot_output_sheet)
        self._cell(g, 1, "起始行", self.var_hot_start_row)
        box = tk.Frame(g, bg=C["card"])
        box.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        tk.Label(box, text=" ", bg=C["card"]).pack()
        self._check(box, "写入表头", self.var_hot_include_headers)

        c4 = self._card(p, "4. 发布到 Cloudflare", "密钥等少改，点标题展开", collapsed=True)
        self._note(c4, "发布请用顶部「发布图库」。这里只改地址和密钥。数据和上次相同会跳过。")
        self.var_cf_url = tk.StringVar()
        self.var_cf_secret = tk.StringVar()
        self._entry(c4, "发布地址（例如 https://gallery.example.com/api/publish-cache）", self.var_cf_url)
        self._entry(c4, "发布密钥", self.var_cf_secret, show="•")
        g = self._row3(c4)
        cell = tk.Frame(g, bg=C["card"])
        cell.grid(row=0, column=0, sticky="ew")
        tk.Label(cell, text="发布哪份数据", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        self.var_cf_source = tk.StringVar(value="all")
        ttk.Combobox(cell, textvariable=self.var_cf_source, values=("all", "hot"), state="readonly", width=16).pack(anchor="w", pady=4)
        self.var_cf_after = tk.BooleanVar(value=False)
        box = tk.Frame(g, bg=C["card"])
        box.grid(row=0, column=1, sticky="ew", padx=8)
        tk.Label(box, text=" ", bg=C["card"]).pack()
        self._check(box, "汇总后自动发布", self.var_cf_after)

        c5 = self._card(p, "5. 定时同步", "关掉窗口就不再跑")
        self._note(c5, "可改成 1 小时(60)、3 小时(180) 或 1 天(1440)。打开软件后若已到期会先跑一轮。")
        g = self._row3(c5)
        self.var_minutes = tk.StringVar(value="1440")
        self._cell(g, 0, "间隔（分钟）", self.var_minutes)
        box = tk.Frame(g, bg=C["card"])
        box.grid(row=0, column=1, columnspan=2, sticky="ew", padx=8)
        tk.Label(box, text="快捷", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        pr = tk.Frame(box, bg=C["card"])
        pr.pack(anchor="w", pady=4)
        for label, m in (("1 小时", 60), ("3 小时", 180), ("6 小时", 360), ("1 天", 1440)):
            StyleBtn(pr, "ghost", text=label, command=lambda x=m: self._set_interval(x)).pack(side="left", padx=(0, 6))
        self.var_sched = tk.BooleanVar(value=False)
        self.var_changed = tk.BooleanVar(value=True)
        self._check(c5, "仅源表有变化时执行", self.var_changed)
        self._check(c5, "启用定时", self.var_sched)
        self.sched_info = tk.Label(c5, text="定时未启动", bg=C["card"], fg=C["muted"], font=FS)
        self.sched_info.pack(anchor="w", pady=4)

        c6 = self._card(p, "6. 抓取字段", "可改列范围", collapsed=True)
        self._note(c6, "填写源表工作表名和列范围。以后换列，改这里即可。")
        fh = tk.Frame(c6, bg="#e8eef7")
        fh.pack(fill="x")
        for t, w in (("字段名", 16), ("工作表", 16), ("范围", 16)):
            tk.Label(fh, text=t, bg="#e8eef7", font=FS, fg=C["muted"], width=w, anchor="w").pack(side="left", padx=4, pady=4)
        self.field_box = tk.Frame(c6, bg=C["card"])
        self.field_box.pack(fill="x")
        self.field_count = tk.Label(c6, text="0 列", bg=C["card"], fg=C["muted"], font=FS)
        self.field_count.pack(anchor="w", pady=4)
        fr = tk.Frame(c6, bg=C["card"])
        fr.pack(anchor="w")
        StyleBtn(fr, "ghost", text="+ 添加字段", command=lambda: self._add_field({})).pack(side="left")
        StyleBtn(fr, "ghost", text="恢复默认字段", command=self._reset_fields).pack(side="left", padx=6)

        c7 = self._card(p, "高级选项", collapsed=True)
        self.var_exclude = tk.StringVar(value="未找到")
        self.var_date_field = tk.StringVar(value="发布日期")
        self.var_sort_field = tk.StringVar(value="点赞")
        self.var_sort_desc = tk.BooleanVar(value=True)
        cred_wrap = tk.Frame(c7, bg=C["card"])
        cred_wrap.pack(fill="x", pady=4)
        tk.Label(cred_wrap, text="服务账号 JSON 文件", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        cred_line = tk.Frame(cred_wrap, bg=C["card"])
        cred_line.pack(fill="x", pady=(3, 0))
        self._input(cred_line, font=MONO, textvariable=self.var_credentials).pack(side="left", fill="x", expand=True, ipady=5)
        StyleBtn(cred_line, "ghost", text="选择文件…", command=self._choose_credentials).pack(side="left", padx=(8, 0))
        g = self._row3(c7)
        self._cell(g, 0, "排除帖文id", self.var_exclude)
        self._cell(g, 1, "日期字段", self.var_date_field)
        self._cell(g, 2, "排序字段", self.var_sort_field)
        self._check(c7, "降序排列", self.var_sort_desc)

    def _add_field(self, item):
        row = tk.Frame(self.field_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        n = self._input(row, font=MONO, width=16)
        n.insert(0, (item or {}).get("name") or "")
        n.pack(side="left", ipady=4, padx=(0, 6))
        s = self._input(row, font=MONO, width=16)
        s.insert(0, (item or {}).get("sheet") or "源数据")
        s.pack(side="left", ipady=4, padx=(0, 6))
        r = self._input(row, font=MONO, width=16)
        r.insert(0, (item or {}).get("range") or "")
        r.pack(side="left", ipady=4, padx=(0, 6))
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_field(row)).pack(side="left")
        row._name, row._sheet, row._range = n, s, r
        self._field_rows.append(row)
        self._upd_fields()

    def _del_field(self, row):
        if row in self._field_rows:
            self._field_rows.remove(row)
        row.destroy()
        if not self._field_rows:
            self._add_field({})
        self._upd_fields()

    def _reset_fields(self):
        for r in list(self._field_rows):
            r.destroy()
        self._field_rows = []
        for f in copy_default_fields():
            self._add_field(f)

    def _upd_fields(self):
        n = sum(1 for r in self._field_rows if r._name.get().strip() and r._range.get().strip())
        self.field_count.configure(text=f"{n} 列")

    def _read_fields(self):
        out = []
        for r in self._field_rows:
            name = r._name.get().strip()
            rng = r._range.get().strip()
            if name and rng:
                out.append({"name": name, "sheet": r._sheet.get().strip() or "源数据", "range": rng})
        return out

    def _build_worksheet_merge(self, p) -> None:
        defaults = asdict(Config())
        self._wm_vars = {key: tk.StringVar(value=str(defaults[key])) for key in (
            "wm_source_url", "wm_source_header_row", "wm_source_start_row", "wm_target_url",
            "wm_target_sheet", "wm_target_header_row", "wm_target_start_row", "wm_target_start_col",
            "cf_profile_id", "wm_gallery_host", "wm_media_type", "wm_release_days", "wm_timezone",
            "wm_date_col", "wm_link_col", "wm_category_col", "wm_title_col", "wm_maker_col", "wm_thumbnail_col", "wm_sheet_filter_mode",
            "wm_publish_source", "wm_direct_url", "wm_direct_sheet", "wm_direct_header_row", "wm_direct_start_row", "wm_schedule_minutes")}
        self._wm_channels=[]
        self._wm_active_channel=''
        self._wm_source_rows=[]
        self._wm_channel_choice=tk.StringVar(value='image')
        self._wm_enabled_vars={kind:tk.BooleanVar(value=kind=='image') for kind in ('image','video','effect_video')}
        self._wm_direct_columns=[]
        c0=self._card(p,'图片、KB视频与效果视频','同一个配置，共用 Cloudflare 账号和域名')
        enabled=tk.Frame(c0,bg=C['card']);enabled.pack(fill='x')
        self._check(enabled,'启用图片汇总',self._wm_enabled_vars['image'])
        self._check(enabled,'启用 KB视频',self._wm_enabled_vars['video'])
        self._check(enabled,'启用效果视频',self._wm_enabled_vars['effect_video'])
        choices=tk.Frame(c0,bg=C['card']);choices.pack(fill='x',pady=(6,4))
        for kind,label in (('image','配置图片'),('video','配置KB视频'),('effect_video','配置效果视频')):
            self._radio(choices, label, self._wm_channel_choice, kind,
                        command=lambda value=kind: self._wm_select_channel(value), font=FB).pack(side='left',padx=(0,18))
        self._wm_channel_label=tk.StringVar(value='正在编辑：图片')
        tk.Label(c0,textvariable=self._wm_channel_label,bg=C['card'],fg=C['head'],font=FB).pack(anchor='w',pady=(6,0))
        self._note(c0,'三个类别分别保存表格、字段和展示规则。发布图库会处理已启用的类别；直接发布无需先汇总。原有视频配置保留为 KB视频。')
        modes=tk.Frame(c0,bg=C['card']);modes.pack(fill='x',pady=6)
        for value,label in (('merged','发布汇总结果'),('direct','直接读取指定表格发布（无需汇总）')):
            self._radio(modes, label, self._wm_vars['wm_publish_source'], value,
                        command=self._wm_update_publish_mode, font=FS).pack(side='left',padx=(0,16))
        direct=self._card(p,'直接发布数据来源','只读取现有 Sheet，不创建或写入汇总表')
        self._wm_direct_card=direct.master
        self._entry(direct,'Google 表格链接（可粘贴带 gid 的工作表链接）',self._wm_vars['wm_direct_url'])
        self._entry(direct,'Sheet 名称（链接带 gid 或只有一个可见 Sheet 时可留空）',self._wm_vars['wm_direct_sheet'])
        row=self._row3(direct)
        self._cell(row,0,'表头行',self._wm_vars['wm_direct_header_row'])
        self._cell(row,1,'数据起始行',self._wm_vars['wm_direct_start_row'])
        self.wm_direct_load_button=StyleBtn(direct,'ghost',text='加载直接发布表头',command=self._load_wm_headers)
        self.wm_direct_load_button.pack(anchor='w',pady=6)
        self._wm_direct_headers_label=tk.StringVar(value='尚未加载表头；可在下方直接填写日期、链接、分类、制作人等列字母。')
        tk.Label(direct,textvariable=self._wm_direct_headers_label,bg=C['card'],fg=C['muted'],font=FS,wraplength=820,justify='left').pack(anchor='w')
        c1 = self._card(p, "1. 来源表格", "按名单排除或只读取指定子 Sheet")
        self._note(c1,'可添加多个 Google 表格链接，按下方模式选择可见子 Sheet。同一类别的来源共用表头、列映射和名称名单；同一表格重复粘贴只读取一次。')
        self.wm_sources_box=tk.Frame(c1,bg=C['card']);self.wm_sources_box.pack(fill='x')
        self._add_wm_source()
        StyleBtn(c1,'ghost',text='+ 添加来源表格链接',command=self._add_wm_source).pack(anchor='w',pady=(6,8))
        row = self._row3(c1)
        self._cell(row, 0, "来源表头行", self._wm_vars["wm_source_header_row"])
        self._cell(row, 1, "来源数据起始行", self._wm_vars["wm_source_start_row"])
        self._note(c1, "隐藏的子 Sheet 自动跳过；目标表格同时作为来源时，三个类别的输出 Sheet 均会自动排除。")
        modes=tk.Frame(c1,bg=C['card']);modes.pack(fill='x',pady=(8,3))
        for mode,label in (('exclude','排除列出的子 Sheet'),('include','只读取列出的子 Sheet')):
            self._radio(modes, label, self._wm_vars['wm_sheet_filter_mode'], mode, font=FS).pack(side='left',padx=(0,18))
        tk.Label(c1, text="子 Sheet 名称（一行一个，按完整名称匹配；只读一个就填写一个名称）", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w", pady=(3, 3))
        self.wm_exclude = self._text_input(c1, height=4)
        self.wm_exclude.pack(fill="x")

        c2 = self._card(p, "2. 自定义汇总表头与来源列", "按下方字段顺序写入")
        self._note(c2, "来源列填写 A、B、C 等列字母，可调整顺序、增删字段或修改汇总表头。读取表头会用第一个符合条件的子 Sheet 重新填充字段。")
        self.wm_load_button = StyleBtn(c2, "ghost", text="读取来源表头", command=self._load_wm_headers)
        self.wm_load_button.pack(anchor="w", pady=(0, 8))
        StyleBtn(c2,'ghost',text='修复目标表已有超链接',command=self._repair_worksheet_links).pack(anchor='w',pady=(0,8))
        head = tk.Frame(c2, bg=C["cream"])
        head.pack(fill="x")
        tk.Label(head, text="汇总表头", bg=C["cream"], fg=C["ink"], font=FS).pack(side="left", fill="x", expand=True, padx=4)
        tk.Label(head, text="来源列", bg=C["cream"], fg=C["ink"], font=FS, width=12).pack(side="left", padx=4)
        tk.Label(head, text="超链接", bg=C["cream"], fg=C["ink"], font=FS, width=8).pack(side="left", padx=4)
        tk.Label(head, text="操作", bg=C["cream"], fg=C["ink"], font=FS, width=8).pack(side="left", padx=4)
        self.wm_columns_box = tk.Frame(c2, bg=C["card"])
        self.wm_columns_box.pack(fill="x")
        StyleBtn(c2, "ghost", text="+ 添加字段", command=self._add_wm_column).pack(anchor="w", pady=(8, 0))

        c3 = self._card(p, "3. 写入目标", "指定表格、Sheet 和行列")
        self._entry(c3, "共用汇总目标 Google 表格链接（每个类别各写一个 Sheet）", self._wm_vars["wm_target_url"])
        self._entry(c3, "当前类别的目标 Sheet 名称（不存在时自动创建）", self._wm_vars["wm_target_sheet"])
        row = self._row3(c3)
        self._cell(row, 0, "目标表头行", self._wm_vars["wm_target_header_row"])
        self._cell(row, 1, "目标数据起始行", self._wm_vars["wm_target_start_row"])
        self._cell(row, 2, "目标起始列", self._wm_vars["wm_target_start_col"])
        self._note(c3, "每次运行重新汇总，不排重；勾选超链接的字段保留原文字与可点击地址，其他字段复制显示文字。仅替换汇总列，并清理上次多余数据，保留其他格式。")
        c4 = self._card(p, "4. Cloudflare 账号与站点", "软件直接推送，无需 Apps Script")
        self._wm_shared_card=c4.master
        self._wm_merge_cards=[c1.master,c2.master,c3.master]
        self._wm_profile_display = tk.StringVar()
        tk.Label(c4, text="发布账号", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        self.wm_profile_combo = ttk.Combobox(c4, textvariable=self._wm_profile_display, state="readonly")
        self.wm_profile_combo.pack(fill="x", pady=4)
        self.wm_profile_combo.bind('<<ComboboxSelected>>', lambda _e: self._wm_profile_changed())
        tk.Label(c4, text="三个类别共用的图库域名", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        self.wm_site_combo = ttk.Combobox(c4, textvariable=self._wm_vars['wm_gallery_host'], state='readonly')
        self.wm_site_combo.pack(fill="x", pady=4)
        StyleBtn(c4, 'ghost', text='管理 Cloudflare 账号、发布范围和站点', command=self._manage_cf_accounts).pack(anchor='w', pady=6)
        self.var_wm_auto_publish = tk.BooleanVar(value=False)
        self._check(c4, '汇总成功后直接发布到所选图库', self.var_wm_auto_publish)
        self._note(c4, '在账号管理中授权此配置发布，并部署多工作表图库。软件上传三个类别的独立数据流，同一域名下互不覆盖。直接发布无需汇总目标表。')
        c5 = self._card(p, '5. 网页展示规则', '直接发布填来源 Sheet 的列；汇总发布填汇总目标列')
        row = self._row3(c5)
        self._wm_media_display = tk.StringVar(value='图片')
        cell = tk.Frame(row, bg=C['card']);cell.grid(row=0,column=0,sticky='ew',padx=(0,8))
        tk.Label(cell,text='素材类型',bg=C['card'],fg=C['muted'],font=FS).pack(anchor='w')
        tk.Label(cell,textvariable=self._wm_media_display,bg=C['card'],fg=C['ink'],font=FB).pack(anchor='w',pady=6)
        self._cell(row,1,'延后几天显示',self._wm_vars['wm_release_days'])
        self._cell(row,2,'日期时区',self._wm_vars['wm_timezone'])
        row = self._row3(c5)
        self._cell(row,0,'日期列（默认 A）',self._wm_vars['wm_date_col'])
        self._cell(row,1,'成品链接列（默认 B）',self._wm_vars['wm_link_col'])
        self._cell(row,2,'分类列（默认 D）',self._wm_vars['wm_category_col'])
        row = self._row3(c5)
        self._cell(row,0,'标题列（可留空）',self._wm_vars['wm_title_col'])
        self._cell(row,1,'缩略图链接列（可留空）',self._wm_vars['wm_thumbnail_col'])
        self._cell(row,2,'制作人列（默认 G，可留空）',self._wm_vars['wm_maker_col'])
        self._note(c5, '图片默认延后 4 天，视频默认 7 天，可修改。20 号图片从 24 号零点开始显示。按分类列自动生成网站目录；视频使用 Drive 缩略图并可在页面播放，图片打开大图。Google Drive 文件需允许网站访问者查看。')
        tk.Label(c5,text='不发布到图库的分类（一行一个，按完整分类名称匹配；留空则全部发布）',bg=C['card'],fg=C['muted'],font=FS).pack(anchor='w',pady=(8,3))
        self.wm_gallery_exclusions=self._text_input(c5,height=3)
        self.wm_gallery_exclusions.pack(fill='x')
        self._note(c5,'填写分类列中的值，例如 SP-灾难类。只过滤当前类别的图库素材，原表和汇总表的数据保留。修改后发布一次生效，定时发布也会应用；填写“未分类”可排除分类为空的素材。')
        c6=self._card(p,'6. 定时汇总与发布','保持软件打开，最小化后继续运行')
        self.var_wm_schedule_enabled=tk.BooleanVar(value=False)
        self.var_wm_schedule_merge=tk.BooleanVar(value=True)
        self.var_wm_schedule_publish=tk.BooleanVar(value=True)
        self._check(c6,'启用本配置定时任务',self.var_wm_schedule_enabled)
        actions=tk.Frame(c6,bg=C['card']);actions.pack(fill='x',pady=3)
        self._check(actions,'定时汇总',self.var_wm_schedule_merge).pack(side='left',padx=(0,20))
        self._check(actions,'定时发布图库',self.var_wm_schedule_publish).pack(side='left')
        self._wm_interval_preset=tk.StringVar(value='180')
        presets=tk.Frame(c6,bg=C['card']);presets.pack(fill='x',pady=5)
        for minutes,label in (('180','3小时'),('360','6小时'),('480','8小时'),('720','12小时'),('custom','自定义')):
            self._radio(presets, label, self._wm_interval_preset, minutes,
                        command=self._wm_choose_interval, font=FS).pack(side='left',padx=(0,12))
        row=self._row3(c6)
        self._cell(row,0,'间隔（分钟，可直接修改）',self._wm_vars['wm_schedule_minutes'])
        self._wm_vars['wm_schedule_minutes'].trace_add('write',self._wm_sync_interval_preset)
        self._note(c6,'默认每 3 小时。勾选两项时，汇总模式先汇总再发布；直接发布模式跳过汇总。上一轮尚未结束时不重复运行；关闭软件停止，重开后恢复已保存的定时。')
        buttons=tk.Frame(c6,bg=C['card']);buttons.pack(fill='x',pady=5)
        StyleBtn(buttons,'primary',text='保存并应用定时',command=self._save).pack(side='left',padx=(0,8))
        StyleBtn(buttons,'ghost',text='立即执行一轮',command=self._run_wm_sync).pack(side='left')
        self.wm_sched_info=tk.Label(c6,text='定时未启动',bg=C['card'],fg=C['muted'],font=FS)
        self.wm_sched_info.pack(anchor='w',pady=4)
        self._refresh_wm_cloudflare()

    def _wm_choose_interval(self) -> None:
        value=self._wm_interval_preset.get()
        if value!='custom':self._wm_vars['wm_schedule_minutes'].set(value)

    def _wm_sync_interval_preset(self,*_args) -> None:
        value=self._wm_vars['wm_schedule_minutes'].get().strip()
        self._wm_interval_preset.set(value if value in ('180','360','480','720') else 'custom')

    def _run_wm_sync(self) -> None:
        err=start_worksheet_sync_job(self._cfg_for_action())
        if err:messagebox.showwarning('定时汇总与发布',err);return
        self._sync_schedulers()
        self._log_n=0
        self._append_log('已加入完整执行队列：按定时操作设置汇总、发布')
        self._set_badge('排队中',C['accent'])

    def _add_wm_source(self, url='') -> None:
        row=tk.Frame(self.wm_sources_box,bg=C['card']);row.pack(fill='x',pady=3)
        if not self._wm_source_rows:
            row._url=self._wm_vars['wm_source_url'];row._url.set(url)
        else:row._url=tk.StringVar(value=url)
        tk.Label(row,text=str(len(self._wm_source_rows)+1),width=3,bg=C['card'],fg=C['muted'],font=FS).pack(side='left')
        self._input(row, font=F, textvariable=row._url).pack(side='left',fill='x',expand=True,ipady=4,padx=(0,6))
        StyleBtn(row,'ghost',text='删除',command=lambda:self._delete_wm_source(row)).pack(side='left')
        self._wm_source_rows.append(row)

    def _delete_wm_source(self, row) -> None:
        values=[entry._url.get() for entry in self._wm_source_rows if entry is not row]
        self._set_wm_sources(values)

    def _set_wm_sources(self, urls) -> None:
        for row in self._wm_source_rows:row.destroy()
        self._wm_source_rows=[]
        for url in urls or ['']:self._add_wm_source(url)

    def _add_wm_column(self, header="", column="", hyperlink=None) -> None:
        row = tk.Frame(self.wm_columns_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        row._header = self._input(row, font=F)
        row._header.insert(0, header)
        row._header.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=4)
        row._column = self._input(row, font=F, width=10)
        row._column.insert(0, column)
        row._column.pack(side="left", padx=(0, 6), ipady=4)
        from sheet_links import is_link_column
        row._link_auto = hyperlink is None
        row._hyperlink = tk.BooleanVar(value=is_link_column({'header': header}) if hyperlink is None else bool(hyperlink))
        tk.Checkbutton(
            row,
            variable=row._hyperlink,
            bg=C['card'],
            fg=C['ink'],
            activebackground=C['card'],
            activeforeground=C['ink'],
            selectcolor='#ffffff',
            highlightthickness=0,
            bd=0,
            width=7,
            command=lambda: setattr(row, '_link_auto', False),
        ).pack(side='left',padx=(0,6))
        row._header.bind('<KeyRelease>',lambda _e: row._hyperlink.set(is_link_column({'header':row._header.get()})) if row._link_auto else None)
        StyleBtn(row, "ghost", text="删除", command=lambda: self._delete_wm_column(row)).pack(side="left")
        self._wm_column_rows.append(row)

    def _delete_wm_column(self, row) -> None:
        self._wm_column_rows.remove(row)
        row.destroy()

    def _set_wm_columns(self, columns) -> None:
        for row in self._wm_column_rows:
            row.destroy()
        self._wm_column_rows = []
        for item in columns:
            self._add_wm_column(str(item.get("header") or ""), str(item.get("column") or ""), item.get('hyperlink'))

    def _wm_channel_settings_from_ui(self) -> dict:
        from sheet_links import is_link_column
        from worksheet_channels import CHANNEL_FIELDS
        urls=[row._url.get().strip() for row in self._wm_source_rows if row._url.get().strip()]
        return {**{key:var.get().strip() for key,var in self._wm_vars.items() if key in CHANNEL_FIELDS},
                'wm_source_urls':urls,'wm_source_url':urls[0] if urls else '',
                'wm_direct_columns':copy.deepcopy(self._wm_direct_columns),
                'wm_gallery_exclude_categories':[line.strip() for line in self.wm_gallery_exclusions.get('1.0','end').splitlines() if line.strip()],
                "wm_exclude_sheets": [line.strip() for line in self.wm_exclude.get("1.0", "end").splitlines() if line.strip()],
                "wm_columns": [{"header": row._header.get().strip(), "column": row._column.get().strip().upper(), 'hyperlink': (is_link_column({'header': row._header.get()}) if row._link_auto else row._hyperlink.get())}
                               for row in self._wm_column_rows]}

    def _wm_store_channel(self) -> None:
        row=next((row for row in self._wm_channels if row['id']==self._wm_active_channel),None)
        if row:row['settings']=self._wm_channel_settings_from_ui()

    def _wm_load_channel(self, kind) -> None:
        row=next(row for row in self._wm_channels if row['id']==kind)
        settings=row['settings'];defaults=asdict(Config())
        from worksheet_channels import SHARED_FIELDS,NAMES
        for key,var in self._wm_vars.items():
            if key not in SHARED_FIELDS:self._set_str(var,settings.get(key),str(defaults[key]))
        self._set_wm_sources(settings.get('wm_source_urls') or ([settings['wm_source_url']] if settings.get('wm_source_url') else []))
        self.wm_exclude.delete('1.0','end');self.wm_exclude.insert('1.0','\n'.join(settings.get('wm_exclude_sheets') or []))
        self._set_wm_columns(settings.get('wm_columns') or [])
        self._wm_direct_columns=copy.deepcopy(settings.get('wm_direct_columns') or [])
        self.wm_gallery_exclusions.delete('1.0','end')
        self.wm_gallery_exclusions.insert('1.0','\n'.join(settings.get('wm_gallery_exclude_categories') or []))
        self._wm_direct_headers_label.set('  |  '.join(f"{item['column']}: {item['header']}" for item in self._wm_direct_columns) or '尚未加载表头；可在下方填写列字母。')
        self._wm_active_channel=kind;self._wm_channel_choice.set(kind)
        self._wm_vars['wm_media_type'].set('image' if kind=='image' else 'video')
        self._wm_media_display.set(NAMES[kind])
        self._wm_channel_label.set(f'正在编辑：{NAMES[kind]}（来源、表头、目标 Sheet 和展示规则分别保存）')
        self._wm_update_publish_mode()

    def _wm_update_publish_mode(self) -> None:
        if not hasattr(self,'_wm_merge_cards'):return
        direct=self._wm_vars['wm_publish_source'].get()=='direct'
        for box in self._wm_merge_cards:
            box.pack_forget()
            if not direct:box.pack(fill='x',padx=16,pady=(0,12),before=self._wm_shared_card)
        self._wm_direct_card.pack_forget()
        if direct:self._wm_direct_card.pack(fill='x',padx=16,pady=(0,12),before=self._wm_shared_card)

    def _wm_select_channel(self, kind) -> None:
        if kind==self._wm_active_channel:return
        self._wm_store_channel();self._wm_load_channel(kind)

    def _wm_settings_from_ui(self) -> dict:
        from worksheet_channels import SHARED_FIELDS
        self._wm_store_channel()
        for row in self._wm_channels:row['enabled']=self._wm_enabled_vars[row['id']].get()
        current=next(row for row in self._wm_channels if row['id']==self._wm_active_channel)
        return {**copy.deepcopy(current['settings']),
                **{key:self._wm_vars[key].get().strip() for key in SHARED_FIELDS if key in self._wm_vars},
                'wm_publish_after_sync':self.var_wm_auto_publish.get(),
                'wm_schedule_enabled':self.var_wm_schedule_enabled.get(),
                'wm_schedule_merge':self.var_wm_schedule_merge.get(),
                'wm_schedule_publish':self.var_wm_schedule_publish.get(),
                'wm_channels':copy.deepcopy(self._wm_channels),'wm_active_channel':self._wm_active_channel}

    def _manage_cf_accounts(self) -> None:
        from cf_account_dialog import CloudflareAccountsDialog
        CloudflareAccountsDialog(self)

    def _refresh_wm_cloudflare(self) -> None:
        from cf_accounts import profiles
        self._wm_profile_ids = {f"{row['name']} · {row['account_id'][-8:]}": row['id'] for row in profiles()}
        self.wm_profile_combo.configure(values=list(self._wm_profile_ids))
        identity = self._wm_vars['cf_profile_id'].get()
        self._wm_profile_display.set(next((label for label,key in self._wm_profile_ids.items() if key==identity),''))
        sites = [site['host'] for site in known_gallery_sites(load_cf_state()) if site.get('profile_id')==identity and site.get('kind')=='summary']
        self.wm_site_combo.configure(values=sites)

    def _wm_profile_changed(self) -> None:
        self._wm_vars['cf_profile_id'].set(self._wm_profile_ids.get(self._wm_profile_display.get(),''))
        self._wm_vars['wm_gallery_host'].set('')
        self._refresh_wm_cloudflare()

    def _wm_media_changed(self) -> None:
        value=self._wm_media_display.get()
        self._wm_select_channel('effect_video' if value=='效果视频' else 'video' if value in ('视频','KB视频') else 'image')

    def _load_wm_headers(self) -> None:
        cfg = self._cfg_for_action()
        from worksheet_channels import channel_config
        kind=self._wm_active_channel
        channel=next(row for row in cfg.wm_channels if row['id']==kind)
        cfg=channel_config(cfg,channel)
        menu_id = self._active_menu_id
        direct=cfg.wm_publish_source=='direct'
        button=self.wm_direct_load_button if direct else self.wm_load_button
        button.configure(state="disabled")
        field='wm_direct_columns' if direct else 'wm_columns'

        def complete(columns=None, error=""):
            button.configure(state="normal")
            if error:
                messagebox.showerror("读取表头失败", error, parent=self)
                return
            item = next((item for item in self._menus if item["id"] == menu_id), None)
            if item:
                for row in item['settings'].get('wm_channels') or []:
                    if row['id']==kind:row['settings'][field]=columns
            if self._active_menu_id == menu_id:
                for row in self._wm_channels:
                    if row['id']==kind:row['settings'][field]=columns
            if self._active_menu_id == menu_id and self._wm_active_channel==kind:
                if direct:
                    self._wm_direct_columns=columns
                    self._wm_direct_headers_label.set('  |  '.join(f"{item['column']}: {item['header']}" for item in columns))
                else:self._set_wm_columns(columns)

        def work():
            try:
                if direct:
                    from summary_direct import read_direct_headers
                    columns=read_direct_headers(cfg)
                else:columns = read_merge_headers(cfg)
                self.after(0, lambda: complete(columns=columns))
            except Exception as exc:
                self.after(0, lambda error=str(exc): complete(error=error))

        threading.Thread(target=work, daemon=True).start()

    def _build_catalog(self, p) -> None:
        c1 = self._card(p, "1. 目录表", "遍历链接列和工作表名称列")
        self._note(
            c1,
            "目录表默认 B 列是表格链接、D 列是要查找的工作表名称；两列都可以修改。"
            "B 列也支持本表内部链接，例如 =HYPERLINK(\"#gid=123456\",\"来源A\")，会按 gid 汇总对应工作表。",
        )
        self.var_catalog_index_url = tk.StringVar()
        self.var_catalog_index_sheet = tk.StringVar()
        self.var_catalog_start_row = tk.StringVar(value="2")
        self.var_catalog_url_col = tk.StringVar(value="B")
        self.var_catalog_sheet_col = tk.StringVar(value="D")
        self._entry(c1, "目录表链接", self.var_catalog_index_url)
        g = self._row3(c1)
        self._cell(g, 0, "目录工作表名", self.var_catalog_index_sheet)
        self._cell(g, 1, "数据起始行", self.var_catalog_start_row)
        g2 = self._row3(c1)
        self._cell(g2, 0, "链接所在列", self.var_catalog_url_col)
        self._cell(g2, 1, "工作表名称所在列", self.var_catalog_sheet_col)
        self.var_catalog_keep_header = tk.BooleanVar(value=False)
        self._check(c1, "每个工作表都保留首行（不勾选则只保留第一份表头）", self.var_catalog_keep_header)
        self.var_catalog_add_source = tk.BooleanVar(value=True)
        self._check(c1, "写入时在 A 列追加来源（用目录里的工作表名称，例如 来源A）", self.var_catalog_add_source)
        self.var_catalog_skip_existing = tk.BooleanVar(value=True)
        self._check(c1, "已有的行跳过，只追加新行（多个目标表之间也全局排重）", self.var_catalog_skip_existing)
        tk.Label(
            c1,
            text="排除这些工作表名称（不汇总。精确匹配；可用 * 通配符，例如 目录 或 分组*）",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(8, 2))
        self.catalog_exclude_box = tk.Frame(c1, bg=C["card"])
        self.catalog_exclude_box.pack(fill="x")
        ex_hint = tk.Frame(c1, bg=C["card"])
        ex_hint.pack(fill="x", pady=4)
        self.catalog_exclude_count = tk.Label(ex_hint, text="0 个排除", bg=C["card"], fg=C["muted"], font=FS)
        self.catalog_exclude_count.pack(side="left")
        StyleBtn(ex_hint, "ghost", text="+ 添加排除", command=self._add_catalog_exclude).pack(side="right")
        self._add_catalog_exclude()

        c2 = self._card(p, "2. 写入目标表")
        self.var_catalog_target_url = tk.StringVar()
        self.var_catalog_output_sheet = tk.StringVar(value="目录汇总")
        self.var_catalog_output_start_row = tk.StringVar(value="1")
        self._entry(c2, "目标表格链接（多个链接用空格或分号分开）", self.var_catalog_target_url)
        g = self._row3(c2)
        self._cell(g, 0, "工作表名", self.var_catalog_output_sheet)
        self._cell(g, 1, "写入起始行", self.var_catalog_output_start_row)
        self.var_catalog_dedupe_col = tk.StringVar()
        self._cell(g, 2, "电话号码排重列", self.var_catalog_dedupe_col)
        self._note(
            c2,
            "可以填写多个目标表链接，程序会先读取全部目标表并全局排重，再把新增行均衡分配写入。"
            "电话号码排重列填写写入结果中的列字母（例如 C）；留空会从表头自动识别电话/手机/号码列。"
            "勾选「追加来源」后，A 列写入目录里的工作表名称，原来的列整体右移。",
        )

        c_date = self._card(p, "3. 日期筛选", "用来限制一张备份表的范围")
        self._note(
            c_date,
            "只写入这个日期范围内的行。表满了就换目标表，把开始日期改成下一段再跑。"
            "日期列填源表列字母；留空则从表头里找带「日期」的列。格式例如 2026-01-01。",
        )
        self.var_catalog_date_filter = tk.BooleanVar(value=True)
        self._check(c_date, "启用日期筛选（不勾选则处理全部日期）", self.var_catalog_date_filter)
        g = self._row3(c_date)
        self.var_catalog_start = tk.StringVar()
        self.var_catalog_end = tk.StringVar()
        self.var_catalog_date_col = tk.StringVar()
        self._cell(g, 0, "开始日期", self.var_catalog_start)
        self._cell(g, 1, "结束日期", self.var_catalog_end)
        self._cell(g, 2, "日期列", self.var_catalog_date_col)

        c3 = self._card(p, "4. 定时汇总", "关掉窗口就不再跑")
        self._note(c3, "目录汇总也支持定时。可改成 1 小时、3 小时或自定义分钟。")
        g = self._row3(c3)
        self.var_catalog_minutes = tk.StringVar(value="180")
        self._cell(g, 0, "间隔（分钟）", self.var_catalog_minutes)
        self.var_catalog_sched = tk.BooleanVar(value=False)
        self._check(c3, "启用目录汇总定时", self.var_catalog_sched)
        self.catalog_sched_info = tk.Label(c3, text="定时未启动", bg=C["card"], fg=C["muted"], font=FS)
        self.catalog_sched_info.pack(anchor="w", pady=4)

    def _add_catalog_exclude(self, value: str = "") -> None:
        row = tk.Frame(self.catalog_exclude_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        e = self._input(row, font=MONO)
        e.insert(0, value)
        e.pack(side="left", fill="x", expand=True, ipady=4)
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_catalog_exclude(row)).pack(side="left", padx=(6, 0))
        row._val = e
        e.bind("<KeyRelease>", lambda _e: self._upd_catalog_exclude_count())
        self._catalog_exclude_rows.append(row)
        self._upd_catalog_exclude_count()

    def _del_catalog_exclude(self, row) -> None:
        if row in self._catalog_exclude_rows:
            self._catalog_exclude_rows.remove(row)
        row.destroy()
        if not self._catalog_exclude_rows:
            self._add_catalog_exclude()
        self._upd_catalog_exclude_count()

    def _upd_catalog_exclude_count(self) -> None:
        n = sum(1 for r in self._catalog_exclude_rows if r._val.get().strip())
        if hasattr(self, "catalog_exclude_count"):
            self.catalog_exclude_count.configure(text=f"{n} 个排除")

    def _read_catalog_exclude(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for row in self._catalog_exclude_rows:
            text = row._val.get().strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        return out

    def _set_catalog_exclude(self, items) -> None:
        self._set_entry_rows(self._catalog_exclude_rows, self._add_catalog_exclude, items, empty=1)
        self._upd_catalog_exclude_count()

    def _posts_default_settings(self) -> dict:
        return {key: value for key, value in asdict(Config()).items() if key.startswith("pa_")}

    def _posts_settings_from_ui(self) -> dict:
        return {
            "pa_list_url": self.var_pa_list_url.get().strip(),
            "pa_list_sheet": self.var_pa_list_sheet.get().strip(),
            "pa_link_col": self.var_pa_link_col.get().strip(),
            "pa_tag_col": self.var_pa_tag_col.get().strip(),
            "pa_start_row": self.var_pa_start_row.get().strip(),
            "pa_sub_sheet": self.var_pa_sub_sheet.get().strip(),
            "pa_source_cols": self._read_posts_cols(),
            "pa_date_col": self.var_pa_date_col.get().strip(),
            "pa_date_filter_enabled": self.var_pa_date_filter.get(),
            "pa_start_date": self.var_pa_start.get().strip(),
            "pa_end_date": self.var_pa_end.get().strip(),
            "pa_include_tag": self.var_pa_include_tag.get(),
            "pa_lookup_enabled": self.var_pa_lookup_enabled.get(),
            "pa_lookup_url": self.var_pa_lookup_url.get().strip(),
            "pa_lookup_sheet": self.var_pa_lookup_sheet.get().strip(),
            "pa_lookup_key_col": self.var_pa_lookup_key.get().strip(),
            "pa_lookup_value_col": self.var_pa_lookup_value.get().strip(),
            "pa_match_col": self.var_pa_match_col.get().strip(),
            "pa_write_library": self.var_pa_write_library.get(),
            "pa_library_write_col": self.var_pa_library_write_col.get().strip(),
            "pa_library_start_row": self.var_pa_library_start_row.get().strip(),
            "pa_target_url": self.var_pa_target_url.get().strip(),
            "pa_output_sheet": self.var_pa_output_sheet.get().strip(),
            "pa_output_start_row": self.var_pa_out_start.get().strip(),
            "pa_include_headers": self.var_pa_include_headers.get(),
            "pa_schedule_enabled": self.var_pa_sched.get(),
            "pa_schedule_minutes": self.var_pa_minutes.get().strip(),
        }

    def _apply_posts_settings(self, s: dict) -> None:
        defaults = self._posts_default_settings()
        self._set_str(self.var_pa_list_url, s.get("pa_list_url"), defaults["pa_list_url"])
        self._set_str(self.var_pa_list_sheet, s.get("pa_list_sheet"), "数据列表")
        self._set_str(self.var_pa_start_row, s.get("pa_start_row"), "2")
        self._set_str(self.var_pa_link_col, s.get("pa_link_col"), "K")
        self._set_str(self.var_pa_tag_col, s.get("pa_tag_col"), "L")
        self._set_bool(self.var_pa_include_tag, s.get("pa_include_tag"), True)
        self._set_str(self.var_pa_sub_sheet, s.get("pa_sub_sheet"), "明细")
        self._set_posts_cols(s.get("pa_source_cols") or defaults["pa_source_cols"])
        self._set_bool(self.var_pa_date_filter, s.get("pa_date_filter_enabled"), True)
        self._set_str(self.var_pa_date_col, s.get("pa_date_col"), "M")
        self._set_str(self.var_pa_start, s.get("pa_start_date"), defaults["pa_start_date"])
        self._set_str(self.var_pa_end, s.get("pa_end_date"))
        self._set_bool(self.var_pa_lookup_enabled, s.get("pa_lookup_enabled"), True)
        self._set_str(self.var_pa_lookup_url, s.get("pa_lookup_url"), defaults["pa_lookup_url"])
        self._set_str(self.var_pa_lookup_sheet, s.get("pa_lookup_sheet"), "对照表")
        self._set_str(self.var_pa_lookup_key, s.get("pa_lookup_key_col"), "B")
        self._set_str(self.var_pa_lookup_value, s.get("pa_lookup_value_col"), "N")
        self._set_str(self.var_pa_match_col, s.get("pa_match_col"), "J")
        self._set_bool(self.var_pa_write_library, s.get("pa_write_library"), True)
        self._set_str(self.var_pa_library_write_col, s.get("pa_library_write_col"), "B")
        self._set_str(self.var_pa_library_start_row, s.get("pa_library_start_row"), "2")
        self._set_str(self.var_pa_target_url, s.get("pa_target_url"), defaults["pa_target_url"])
        self._set_str(self.var_pa_output_sheet, s.get("pa_output_sheet"), "整合")
        self._set_str(self.var_pa_out_start, s.get("pa_output_start_row"), "2")
        self._set_bool(self.var_pa_include_headers, s.get("pa_include_headers"))
        self._set_bool(self.var_pa_sched, s.get("pa_schedule_enabled"))
        self._set_str(self.var_pa_minutes, s.get("pa_schedule_minutes"), "120")

    def _build_posts(self, p) -> None:
        c1 = self._card(p, "1. 数据列表", "列出要汇总的明细表链接")
        self._note(
            c1,
            "主表里一列是各明细表格链接，一列是来源标记（写入整合表）。"
            "链接列可以是完整网址，也可以是蓝字超链接。服务账号用软件顶部配置的 JSON。",
        )
        self.var_pa_list_url = tk.StringVar()
        self.var_pa_list_sheet = tk.StringVar(value="数据列表")
        self.var_pa_start_row = tk.StringVar(value="2")
        self.var_pa_link_col = tk.StringVar(value="K")
        self.var_pa_tag_col = tk.StringVar(value="L")
        self._entry(c1, "数据列表表格链接", self.var_pa_list_url)
        g = self._row3(c1)
        self._cell(g, 0, "工作表名", self.var_pa_list_sheet)
        self._cell(g, 1, "数据起始行", self.var_pa_start_row)
        g2 = self._row3(c1)
        self._cell(g2, 0, "链接所在列", self.var_pa_link_col)
        self._cell(g2, 1, "来源标记列", self.var_pa_tag_col)
        self.var_pa_include_tag = tk.BooleanVar(value=True)
        self._check(c1, "把来源标记列写入整合表（接在明细列后面）", self.var_pa_include_tag)

        c2 = self._card(p, "2. 明细表", "每个链接打开后读取这个工作表")
        self.var_pa_sub_sheet = tk.StringVar(value="明细")
        self._entry(c2, "明细工作表名", self.var_pa_sub_sheet)
        self._note(c2, "下面按顺序读取这些列，写入整合表 A 列起。默认 J、M、O、A、L、E、N。")
        self.posts_col_box = tk.Frame(c2, bg=C["card"])
        self.posts_col_box.pack(fill="x")
        col_hint = tk.Frame(c2, bg=C["card"])
        col_hint.pack(fill="x", pady=4)
        self.posts_col_count = tk.Label(col_hint, text="0 列", bg=C["card"], fg=C["muted"], font=FS)
        self.posts_col_count.pack(side="left")
        StyleBtn(col_hint, "ghost", text="+ 添加列", command=self._add_posts_col).pack(side="right")
        for letter in ("J", "M", "O", "A", "L", "E", "N"):
            self._add_posts_col(letter)

        c3 = self._card(p, "3. 日期筛选", "限制写入范围")
        self._note(c3, "按明细表里的日期列筛选。只填开始日期则从这天起（含当天）。格式例如 2026-01-01。")
        self.var_pa_date_filter = tk.BooleanVar(value=True)
        self._check(c3, "启用日期筛选", self.var_pa_date_filter)
        g = self._row3(c3)
        self.var_pa_date_col = tk.StringVar(value="M")
        self.var_pa_start = tk.StringVar()
        self.var_pa_end = tk.StringVar()
        self._cell(g, 0, "日期列", self.var_pa_date_col)
        self._cell(g, 1, "开始日期", self.var_pa_start)
        self._cell(g, 2, "结束日期", self.var_pa_end)

        c4 = self._card(p, "4. 对照表查找", "用明细表一列去对照表查找")
        self.var_pa_lookup_enabled = tk.BooleanVar(value=True)
        self._check(c4, "启用对照表查找（结果追加在整合表最后一列）", self.var_pa_lookup_enabled)
        self.var_pa_lookup_url = tk.StringVar()
        self._entry(c4, "对照表表格链接", self.var_pa_lookup_url)
        g = self._row3(c4)
        self.var_pa_lookup_sheet = tk.StringVar(value="对照表")
        self.var_pa_lookup_key = tk.StringVar(value="B")
        self.var_pa_lookup_value = tk.StringVar(value="N")
        self._cell(g, 0, "工作表名", self.var_pa_lookup_sheet)
        self._cell(g, 1, "查找列", self.var_pa_lookup_key)
        self._cell(g, 2, "取值列", self.var_pa_lookup_value)
        g2 = self._row3(c4)
        self.var_pa_match_col = tk.StringVar(value="J")
        self.var_pa_library_write_col = tk.StringVar(value="B")
        self.var_pa_library_start_row = tk.StringVar(value="2")
        self._cell(g2, 0, "明细表用来对照的列", self.var_pa_match_col)
        self._cell(g2, 1, "写入对照表哪一列", self.var_pa_library_write_col)
        self._cell(g2, 2, "新链接插入起始行", self.var_pa_library_start_row)
        self.var_pa_write_library = tk.BooleanVar(value=True)
        self._check(c4, "同时把明细表新链接写入对照表（按查找列排重，已有的跳过）", self.var_pa_write_library)
        self._note(c4, "整合表流程不变。默认用明细表一列对照查找；新链接从上面设置的起始行插入，原有数据往下移。")

        c5 = self._card(p, "5. 写入目标表")
        self.var_pa_target_url = tk.StringVar()
        self._entry(c5, "目标表格链接（留空则写入数据列表同一张表）", self.var_pa_target_url)
        g = self._row3(c5)
        self.var_pa_output_sheet = tk.StringVar(value="整合")
        self.var_pa_out_start = tk.StringVar(value="2")
        self._cell(g, 0, "工作表名", self.var_pa_output_sheet)
        self._cell(g, 1, "写入起始行", self.var_pa_out_start)
        self.var_pa_include_headers = tk.BooleanVar(value=False)
        self._check(c5, "写入表头（默认不写，从第 2 行起覆盖 A 列往后）", self.var_pa_include_headers)

        c6 = self._card(p, "6. 定时汇总", "关掉窗口就不再跑")
        self._note(c6, "可改成 1 小时、2 小时或自定义分钟数。")
        g = self._row3(c6)
        self.var_pa_minutes = tk.StringVar(value="120")
        self._cell(g, 0, "间隔（分钟）", self.var_pa_minutes)
        self.var_pa_sched = tk.BooleanVar(value=False)
        self._check(c6, "启用多表汇总定时", self.var_pa_sched)
        self.posts_sched_info = tk.Label(c6, text="定时未启动", bg=C["card"], fg=C["muted"], font=FS)
        self.posts_sched_info.pack(anchor="w", pady=4)

    def _add_posts_col(self, value: str = "") -> None:
        row = tk.Frame(self.posts_col_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        e = self._input(row, font=MONO, width=8)
        e.insert(0, value)
        e.pack(side="left", ipady=4)
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_posts_col(row)).pack(side="left", padx=(6, 0))
        row._val = e
        e.bind("<KeyRelease>", lambda _e: self._upd_posts_col_count())
        self._posts_col_rows.append(row)
        self._upd_posts_col_count()

    def _del_posts_col(self, row) -> None:
        if row in self._posts_col_rows:
            self._posts_col_rows.remove(row)
        row.destroy()
        if not self._posts_col_rows:
            self._add_posts_col()
        self._upd_posts_col_count()

    def _upd_posts_col_count(self) -> None:
        n = sum(1 for r in self._posts_col_rows if r._val.get().strip())
        if hasattr(self, "posts_col_count"):
            self.posts_col_count.configure(text=f"{n} 列")

    def _read_posts_cols(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for row in self._posts_col_rows:
            text = row._val.get().strip().upper()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        return out

    def _set_posts_cols(self, items) -> None:
        self._set_entry_rows(self._posts_col_rows, self._add_posts_col, items or ["J", "M", "O", "A", "L", "E", "N"], empty=1)
        self._upd_posts_col_count()

    def _build_align(self, p) -> None:
        c1 = self._card(p, "1. 数据源表格链接", collapsed=True)
        self._note(c1, "每行填一个数据源。工作表名称填源表里的 sheet 名（如 8月份）；留空则读第一个工作表。")
        box, count, ph = self._src_table(c1, ["工作表名称", "表格链接"], "+ 添加数据源", "_align_rows", "例如：8月份")
        self.align_box, self.align_count = box, count
        g = self._row3(c1)
        self.var_align_source_sheet = tk.StringVar()
        self.var_align_header_row = tk.StringVar(value="1")
        self._cell(g, 0, "默认工作表名", self.var_align_source_sheet)
        self._cell(g, 1, "表头所在行", self.var_align_header_row)

        c2 = self._card(p, "2. 字段映射", "和数据筛选一样可增减字段，可按链接单独配置")
        self._note(c2, "每一行：目标字段（写入列名）← 源字段（源表表头）。上面选「某个链接」后，只改这个链接的映射，不影响其它链接。")
        profile_row = tk.Frame(c2, bg=C["card"])
        profile_row.pack(fill="x", pady=4)
        tk.Label(profile_row, text="配置范围", bg=C["card"], fg=C["muted"], font=FS).pack(side="left")
        self.var_align_profile = tk.StringVar(value="所有链接的默认映射")
        self.align_profile_combo = ttk.Combobox(profile_row, textvariable=self.var_align_profile, state="readonly", width=42)
        self.align_profile_combo.pack(side="left", padx=8)
        self.align_profile_combo.bind("<<ComboboxSelected>>", lambda _e: self._switch_align_profile())
        StyleBtn(profile_row, "ghost", text="刷新链接列表", command=self._refresh_align_profiles).pack(side="left")
        fh = tk.Frame(c2, bg="#eff6ff")
        fh.pack(fill="x")
        tk.Label(fh, text="目标字段", bg="#eff6ff", font=FS, fg=C["muted"], width=18, anchor="w").pack(side="left", padx=4, pady=4)
        tk.Label(fh, text="源字段", bg="#eff6ff", font=FS, fg=C["muted"], width=18, anchor="w").pack(side="left", padx=4, pady=4)
        self.align_map_box = tk.Frame(c2, bg=C["card"])
        self.align_map_box.pack(fill="x")
        self._align_map_rows: list[tk.Frame] = []
        hr = tk.Frame(c2, bg=C["card"])
        hr.pack(fill="x", pady=4)
        self.align_header_count = tk.Label(hr, text="0 列", bg=C["card"], fg=C["muted"], font=FS)
        self.align_header_count.pack(side="left")
        StyleBtn(hr, "ghost", text="+ 添加字段", command=lambda: self._add_align_map_row("", "")).pack(side="right")
        StyleBtn(hr, "ghost", text="从源表读取表头", command=self._peek_headers).pack(side="right", padx=6)

        c3 = self._card(p, "3. 目标表", collapsed=True)
        self.var_align_target_url = tk.StringVar()
        self._entry(c3, "目标表格链接", self.var_align_target_url)
        g = self._row3(c3)
        self.var_align_output_sheet = tk.StringVar(value="对齐结果")
        self.var_align_start_row = tk.StringVar(value="1")
        self.var_align_include_headers = tk.BooleanVar(value=True)
        self._cell(g, 0, "工作表名", self.var_align_output_sheet)
        self._cell(g, 1, "起始行", self.var_align_start_row)
        box = tk.Frame(g, bg=C["card"])
        box.grid(row=0, column=2, sticky="ew", padx=8)
        tk.Label(box, text=" ", bg=C["card"]).pack()
        self._check(box, "写入表头", self.var_align_include_headers)

        c4 = self._card(p, "4. 定时同步", "需保持本程序开着", collapsed=True)
        self._note(c4, "到点后按上面的源表重新对齐写入。勾选「仅源表变化时执行」则没改动就跳过。")
        g = self._row3(c4)
        self.var_align_minutes = tk.StringVar(value="60")
        self._cell(g, 0, "间隔（分钟）", self.var_align_minutes)
        self.var_align_sched = tk.BooleanVar(value=False)
        self.var_align_changed = tk.BooleanVar(value=True)
        self._check(c4, "仅源表有变化时执行", self.var_align_changed)
        self._check(c4, "启用定时", self.var_align_sched)
        self.align_sched_info = tk.Label(c4, text="定时未启动", bg=C["card"], fg=C["muted"], font=FS)
        self.align_sched_info.pack(anchor="w", pady=4)

    def _build_video(self, p) -> None:
        c1 = self._card(p, "1. 源表", "B 列视频链接，H 列制作人")
        self.vd_src_note = tk.Label(
            c1,
            text="读取源表：A 列日期、B 列视频链接、H 列制作人（列字母可改）。B 列可以是蓝字文件名，程序会读取单元格里的超链接（Drive / YouTube），再写入另一张表的「日志表」和「数据表」。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=820,
            justify="left",
        )
        self.vd_src_note.pack(anchor="w", pady=(0, 8))
        self.var_vd_source_url = tk.StringVar()
        self.var_vd_source_sheet = tk.StringVar()
        self.var_vd_start_row = tk.StringVar(value="2")
        self._entry(c1, "源表格链接", self.var_vd_source_url)
        g = self._row3(c1)
        self._cell(g, 0, "工作表名称（多个用逗号分隔）", self.var_vd_source_sheet)
        self._cell(g, 1, "数据起始行", self.var_vd_start_row)

        c2 = self._card(p, "2. 源表列（默认可改）", "列字母可改")
        self.vd_col_note = tk.Label(
            c2,
            text="列用字母。默认：A=日期，B=视频链接，H=制作人，E=类型。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=820,
            justify="left",
        )
        self.vd_col_note.pack(anchor="w", pady=(0, 8))
        g = self._row3(c2)
        self.var_vd_col_date = tk.StringVar(value="A")
        self.var_vd_col_link = tk.StringVar(value="B")
        self.var_vd_col_name = tk.StringVar(value="H")
        self._cell(g, 0, "日期列", self.var_vd_col_date)
        self.vd_link_cell = self._cell(g, 1, "视频链接列", self.var_vd_col_link)
        self._cell(g, 2, "制作人列", self.var_vd_col_name)
        g2 = self._row3(c2)
        self.var_vd_col_type = tk.StringVar(value="E")
        self._cell(g2, 0, "类型列", self.var_vd_col_type)
        self.vd_extra_col_box = tk.Frame(c2, bg=C["card"])
        self.vd_extra_col_box.pack(fill="x", pady=(6, 0))
        StyleBtn(c2, "ghost", text="+ 添加分类列", command=self._add_vd_extra_col).pack(anchor="e", pady=4)

        c_date = self._card(p, "3. 日期筛选", "按日期列筛选")
        self._note(
            c_date,
            "填写开始、结束日期后：只查询、只汇总这个范围内的视频。留空则不限日期。格式例如 2026-08-01。",
        )
        g = self._row3(c_date)
        self.var_vd_start = tk.StringVar()
        self.var_vd_end = tk.StringVar()
        self.var_vd_date_filter_enabled = tk.BooleanVar(value=True)
        self._check(c_date, "启用日期筛选（不勾选则处理全部日期）", self.var_vd_date_filter_enabled)
        self._cell(g, 0, "开始日期", self.var_vd_start)
        self._cell(g, 1, "结束日期", self.var_vd_end)

        c_type = self._card(p, "4. 类型 / 分类", "视频用来筛选；分类汇总用来统计")
        self.vd_type_note = tk.Label(
            c_type,
            text="视频时长：未勾选按普通数量统计；勾选“按时长分类”后，按时间规则拆分成多个子列。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=760,
            justify="left",
        )
        self.vd_type_note.pack(anchor="w", pady=(0, 6))
        self.vd_type_head = tk.Frame(c_type, bg="#eff6ff")
        self.vd_type_head.pack(fill="x", pady=(0, 2))
        tk.Label(self.vd_type_head, text="分类名称", bg="#eff6ff", fg=C["muted"], font=FS, anchor="w").pack(side="left", fill="x", expand=True, padx=6, pady=3)
        tk.Label(self.vd_type_head, text="总计数", bg="#eff6ff", fg=C["muted"], font=FS, width=8).pack(side="left")
        tk.Label(self.vd_type_head, text="逐条计数", bg="#eff6ff", fg=C["muted"], font=FS, width=10).pack(side="left")
        tk.Label(self.vd_type_head, text="按时长分类", bg="#eff6ff", fg=C["muted"], font=FS, width=12).pack(side="left")
        tk.Label(self.vd_type_head, text="", bg="#eff6ff", width=8).pack(side="left")
        self.vd_type_box = tk.Frame(c_type, bg=C["card"])
        self.vd_type_box.pack(fill="x")
        self.vd_video_filter_row = tk.Frame(c_type, bg=C["card"])
        self.vd_video_filter_row.pack(fill="x", pady=4)
        tk.Label(self.vd_video_filter_row, text="类型操作", bg=C["card"], fg=C["muted"], font=FS).pack(side="left")
        self.var_vd_type_filter_mode = tk.StringVar(value="只包含这些类型")
        ttk.Combobox(self.vd_video_filter_row, textvariable=self.var_vd_type_filter_mode, values=("只包含这些类型", "排除这些类型", "不筛选类型"), state="readonly", width=22).pack(side="left", padx=8)
        hint = tk.Frame(c_type, bg=C["card"])
        hint.pack(fill="x", pady=6)
        self.vd_type_count = tk.Label(hint, text="0 个分类", bg=C["card"], fg=C["muted"], font=FS)
        self.vd_type_count.pack(side="left")
        StyleBtn(hint, "ghost", text="+ 添加分类", command=self._add_vd_type).pack(side="right")
        StyleBtn(hint, "ghost", text="清空", command=self._clear_vd_types).pack(side="right", padx=6)
        self.vd_wildcard_note = tk.Label(
            c_type,
            text="自定义汇总支持包含匹配和通配符：wsp 会统计所有带 wsp 的分类；口播* 匹配以口播开头的。视频提取按精确分类筛选，不用通配符。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=760,
            justify="left",
        )
        self.vd_wildcard_note.pack(anchor="w", pady=(0, 4))
        self._add_vd_type()
        self._add_vd_type()

        self.vd_custom_box = tk.Frame(c_type, bg=C["card"])
        self.vd_mode_row = tk.Frame(self.vd_custom_box, bg=C["card"])
        self.vd_mode_row.pack(fill="x", pady=(8, 4))
        tk.Label(self.vd_mode_row, text="统计方式", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        self.var_vd_category_mode = tk.StringVar(value="分类单独成列，未分类和其他归入其余")
        self.vd_category_mode_combo = ttk.Combobox(
            self.vd_mode_row,
            textvariable=self.var_vd_category_mode,
            values=(
                "分类单独成列，未分类和其他归入其余",
                "这些分类不统计，未分类和其他全部归入其余",
            ),
            state="readonly",
        )
        self.vd_category_mode_combo.pack(fill="x", pady=(3, 0), ipady=3)
        self.vd_category_mode_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_vd_category_mode())
        self.vd_exclude_wrap = tk.Frame(self.vd_custom_box, bg=C["card"])
        self.vd_exclude_wrap.pack(fill="x")
        tk.Label(self.vd_exclude_wrap, text="额外不统计的分类（不要和上面重复）", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w", pady=(8, 2))
        self.vd_exclude_box = tk.Frame(self.vd_exclude_wrap, bg=C["card"])
        self.vd_exclude_box.pack(fill="x")
        ex_hint = tk.Frame(self.vd_exclude_wrap, bg=C["card"])
        ex_hint.pack(fill="x", pady=4)
        self.vd_exclude_count = tk.Label(ex_hint, text="0 个排除", bg=C["card"], fg=C["muted"], font=FS)
        self.vd_exclude_count.pack(side="left")
        StyleBtn(ex_hint, "ghost", text="+ 添加排除", command=self._add_vd_exclude).pack(side="right")
        g_other = self._row3(self.vd_custom_box)
        self.var_vd_other_category = tk.StringVar()
        self.vd_other_cell = self._cell(g_other, 0, "其余归入（含未填写分类，例如：图片）", self.var_vd_other_category)
        self.var_vd_empty_to_other = tk.BooleanVar(value=True)
        self.vd_empty_check = self._check(self.vd_custom_box, "没有分类的也归入「其余」", self.var_vd_empty_to_other)
        self.vd_custom_help = tk.Label(
            self.vd_custom_box,
            text="方式一：上面 FB/口播 等各占一列，没填分类的算进「其余」。方式二：上面这些分类完全不统计，剩下的（含空白）全部算进「其余」。不要把同一批分类同时填进两处。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=760,
            justify="left",
        )
        self.vd_custom_help.pack(anchor="w", pady=(4, 0))

        c3 = self._card(p, "5. 写入目标表", "视频写日志+数据；分类汇总只写数据")
        self.vd_dest_note = tk.Label(
            c3,
            text="视频提取时长会写日志表并按时长汇总；分类汇总不写日志，只按每天每个分类计条数。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=760,
            justify="left",
        )
        self.vd_dest_note.pack(anchor="w", pady=(0, 6))
        self.var_vd_dest_url = tk.StringVar()
        self._entry(c3, "写入表格链接", self.var_vd_dest_url)
        g = self._row3(c3)
        self.var_vd_log_sheet = tk.StringVar(value="日志表")
        self.var_vd_report_sheet = tk.StringVar(value="数据表")
        self.var_vd_out_start_row = tk.StringVar(value="1")
        self._cell(g, 0, "数据表名称", self.var_vd_report_sheet)
        self.vd_log_cell = self._cell(g, 1, "日志表名称（视频模板）", self.var_vd_log_sheet)
        self._cell(g, 2, "写入起始行", self.var_vd_out_start_row)
        self.vd_video_opts = self._row3(c3)
        self.var_vd_unit = tk.StringVar(value="30")
        self.var_vd_count_mode = tk.StringVar(value="汇总总秒数 ÷ 30")
        self.var_vd_include_headers = tk.BooleanVar(value=True)
        self._cell(self.vd_video_opts, 0, "汇总除数（仅视频模板）", self.var_vd_unit)
        mode_box = tk.Frame(self.vd_video_opts, bg=C["card"])
        mode_box.grid(row=0, column=1, sticky="ew", padx=8)
        tk.Label(mode_box, text="计数方式（统计模式）", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        ttk.Combobox(
            mode_box,
            textvariable=self.var_vd_count_mode,
            values=("汇总总秒数 ÷ 30", "逐条视频按30秒计数", "按时长区间统计数量"),
            state="readonly",
        ).pack(fill="x", pady=(3, 0), ipady=4)
        box = tk.Frame(self.vd_video_opts, bg=C["card"])
        box.grid(row=0, column=2, sticky="ew", padx=8)
        tk.Label(box, text=" ", bg=C["card"]).pack()
        self._check(box, "写入表头", self.var_vd_include_headers)

        self.vd_duration_rule_wrap = tk.Frame(c3, bg=C["card"])
        self.vd_duration_rule_wrap.pack(fill="x", pady=(8, 0))
        tk.Label(
            self.vd_duration_rule_wrap,
            text="时间规则同时用于逐条计数和勾选了“按时长分类”的分类列。每条原视频只进入一个区间；未勾选的分类只显示普通数量。下限表示“大于”，上限表示“不超过”；留空表示不限。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=820,
            justify="left",
        ).pack(anchor="w", pady=(0, 4))
        rule_head = tk.Frame(self.vd_duration_rule_wrap, bg="#eff6ff")
        rule_head.pack(fill="x", pady=(0, 2))
        tk.Label(rule_head, text="显示名称", bg="#eff6ff", fg=C["muted"], font=FS, anchor="w").pack(side="left", fill="x", expand=True, padx=6, pady=3)
        tk.Label(rule_head, text="大于（秒）", bg="#eff6ff", fg=C["muted"], font=FS, width=14).pack(side="left")
        tk.Label(rule_head, text="不超过（秒）", bg="#eff6ff", fg=C["muted"], font=FS, width=14).pack(side="left")
        tk.Label(rule_head, text="", bg="#eff6ff", width=8).pack(side="left")
        self.vd_duration_rule_box = tk.Frame(self.vd_duration_rule_wrap, bg=C["card"])
        self.vd_duration_rule_box.pack(fill="x")
        rule_hint = tk.Frame(self.vd_duration_rule_wrap, bg=C["card"])
        rule_hint.pack(fill="x", pady=4)
        self.vd_duration_rule_count = tk.Label(rule_hint, text="0 个时长区间", bg=C["card"], fg=C["muted"], font=FS)
        self.vd_duration_rule_count.pack(side="left")
        StyleBtn(rule_hint, "ghost", text="+ 添加时间规则", command=self._add_vd_duration_rule).pack(side="right")
        self._set_vd_duration_rules(self._default_vd_duration_rules())

        self.vd_report_cat_wrap = tk.Frame(c3, bg=C["card"])
        self.vd_report_cat_wrap.pack(fill="x", pady=(8, 0))
        tk.Label(
            self.vd_report_cat_wrap,
            text="数据表分类列：未勾选“按时长分类”时按视频个数计；勾选后按上面的时间区间展开。留空则按第 4 节分类自动建列。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(0, 4))
        self.vd_report_cat_box = tk.Frame(self.vd_report_cat_wrap, bg=C["card"])
        self.vd_report_cat_box.pack(fill="x")
        rc_hint = tk.Frame(self.vd_report_cat_wrap, bg=C["card"])
        rc_hint.pack(fill="x", pady=4)
        self.vd_report_cat_count = tk.Label(rc_hint, text="0 个额外列", bg=C["card"], fg=C["muted"], font=FS)
        self.vd_report_cat_count.pack(side="left")
        StyleBtn(rc_hint, "ghost", text="+ 添加分类列", command=self._add_vd_report_cat).pack(side="right")
        self._add_vd_report_cat()
        self.vd_force_refresh_btn = StyleBtn(
            c3,
            "ghost",
            text="强制刷新表头并汇总",
            command=self._force_refresh_vd_header,
        )
        self.vd_force_refresh_btn.pack(anchor="e", pady=(8, 2))

        c_sched = self._card(p, "6. 定时提取", "需保持本程序开着")
        self.vd_sched_note = tk.Label(
            c_sched,
            text="到点后自动读取新链接、追加日志，并重新生成数据表。首次启用约 12 秒后执行一轮。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=820,
            justify="left",
        )
        self.vd_sched_note.pack(anchor="w", pady=(0, 8))
        g3 = self._row3(c_sched)
        self.var_vd_schedule_minutes = tk.StringVar(value="180")
        self._cell(g3, 0, "间隔（分钟）", self.var_vd_schedule_minutes)
        self.var_vd_schedule_enabled = tk.BooleanVar(value=False)
        self.vd_sched_check = self._check(c_sched, "启用视频时长定时", self.var_vd_schedule_enabled)
        self.vd_sched_info = tk.Label(c_sched, text="定时未启动", bg=C["card"], fg=C["muted"], font=FS)
        self.vd_sched_info.pack(anchor="w", pady=4)

        c4 = self._card(p, "说明", collapsed=True)
        self.vd_help_note = tk.Label(
            c4,
            text="日志表：A 日期、B 链接、C 名字、D 时长(秒)、E 类型、F 备注（未识别原因）。已跑过的链接下次自动跳过。"
            "数据表：普通分类显示一个数量列；勾选按时长分类后，显示分类父表头及各时间区间子列。修改配置后可强制刷新表头、数据和样式。"
            "未识别常见原因：不是视频、没有权限、Drive 还没生成时长。源表和 Drive 文件都要共享给服务账号。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=820,
            justify="left",
        )
        self.vd_help_note.pack(anchor="w", pady=(0, 8))

    def _build_sheet_metrics(self, p) -> None:
        c1 = self._card(p, "1. 来源表格", "来源与目标完全独立配置")
        self._note(c1, "ALL 扫描所有未排除工作表；SINGLE 只读取指定工作表。来源与目标可以是不同表格，也可以是同一表格。")
        self.var_sm_source_url = tk.StringVar()
        self._entry(c1, "来源 Google 表格链接 / Spreadsheet ID", self.var_sm_source_url)
        mode_row = self._row3(c1)
        self.var_sm_source_mode = tk.StringVar(value="扫描全部工作表")
        mode_box = tk.Frame(mode_row, bg=C["card"])
        mode_box.grid(row=0, column=0, sticky="ew")
        tk.Label(mode_box, text="来源模式", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        ttk.Combobox(mode_box, textvariable=self.var_sm_source_mode, values=("扫描全部工作表", "只扫描指定工作表"), state="readonly").pack(fill="x", ipady=4, pady=(3, 0))
        self.var_sm_source_sheet = tk.StringVar()
        self._cell(mode_row, 1, "指定工作表名称（SINGLE）", self.var_sm_source_sheet)
        self.var_sm_exclude_exact = tk.StringVar()
        self._cell(mode_row, 2, "完全排除（逗号分隔）", self.var_sm_exclude_exact)
        self.var_sm_exclude_contains = tk.StringVar()
        self._entry(c1, "名称包含这些关键词则排除（逗号分隔）", self.var_sm_exclude_contains)

        c2 = self._card(p, "2. 来源结构", "所有行号、列字母均可独立修改")
        self._note(c2, "每个来源指标组按固定列宽重复。系统从“第一组指标起始列”开始，每隔一组列数读取代码、名字和前 N 项指标。")
        g = self._row3(c2)
        self.var_sm_source_code_row = tk.StringVar(value="1")
        self.var_sm_source_name_row = tk.StringVar(value="2")
        self.var_sm_source_date_col = tk.StringVar(value="A")
        self._cell(g, 0, "代码所在行", self.var_sm_source_code_row)
        self._cell(g, 1, "名字所在行", self.var_sm_source_name_row)
        self._cell(g, 2, "日期所在列", self.var_sm_source_date_col)
        g = self._row3(c2)
        self.var_sm_source_data_start_row = tk.StringVar(value="5")
        self.var_sm_source_metric_start_col = tk.StringVar(value="F")
        self.var_sm_source_group_width = tk.StringVar(value="6")
        self._cell(g, 0, "实际数据起始行", self.var_sm_source_data_start_row)
        self._cell(g, 1, "第一组指标起始列", self.var_sm_source_metric_start_col)
        self._cell(g, 2, "来源每组列数", self.var_sm_source_group_width)
        g = self._row3(c2)
        self.var_sm_source_metric_count = tk.StringVar(value="4")
        self._cell(g, 0, "每组读取指标数", self.var_sm_source_metric_count)

        c3 = self._card(p, "3. 写入目标表格", "不会插入、删除、移动或重新格式化")
        self.var_sm_target_url = tk.StringVar()
        self.var_sm_target_sheet = tk.StringVar()
        self._entry(c3, "目标 Google 表格链接 / Spreadsheet ID", self.var_sm_target_url)
        self._entry(c3, "目标工作表名称", self.var_sm_target_sheet)

        c4 = self._card(p, "4. 目标结构", "目标行列独立于来源设置")
        g = self._row3(c4)
        self.var_sm_target_name_col = tk.StringVar(value="A")
        self.var_sm_target_code_col = tk.StringVar(value="B")
        self.var_sm_target_date_row = tk.StringVar(value="3")
        self._cell(g, 0, "名字所在列", self.var_sm_target_name_col)
        self._cell(g, 1, "代码所在列", self.var_sm_target_code_col)
        self._cell(g, 2, "日期标题所在行", self.var_sm_target_date_row)
        g = self._row3(c4)
        self.var_sm_target_data_start_row = tk.StringVar(value="7")
        self.var_sm_target_group_start_col = tk.StringVar(value="L")
        self.var_sm_target_group_width = tk.StringVar(value="8")
        self._cell(g, 0, "目标数据起始行", self.var_sm_target_data_start_row)
        self._cell(g, 1, "第一个日期组起始列", self.var_sm_target_group_start_col)
        self._cell(g, 2, "目标每组列数", self.var_sm_target_group_width)
        g = self._row3(c4)
        self.var_sm_target_write_count = tk.StringVar(value="4")
        self._cell(g, 0, "每组允许写入前几列", self.var_sm_target_write_count)

        c5 = self._card(p, "5. 匹配与安全", "可选预览，也可直接正式同步")
        self._note(c5, "只有名字、代码、日期三项同时匹配才写入；来源空值、目标公式、重复来源和后方禁止列全部跳过。")
        g = self._row3(c5)
        self.var_sm_date_mode = tk.StringVar(value="按月日匹配")
        date_box = tk.Frame(g, bg=C["card"])
        date_box.grid(row=0, column=0, sticky="ew")
        tk.Label(date_box, text="日期匹配模式", bg=C["card"], fg=C["muted"], font=FS).pack(anchor="w")
        ttk.Combobox(date_box, textvariable=self.var_sm_date_mode, values=("按月日匹配", "按完整年月日匹配"), state="readonly").pack(fill="x", ipady=4, pady=(3, 0))
        self.var_sm_batch_size = tk.StringVar(value="100")
        self._cell(g, 1, "每批更新范围数", self.var_sm_batch_size)

    def _build_roster(self, p) -> None:
        from roster_fill import DEFAULT_COLUMNS

        c1 = self._card(p, "1. 配置表", "提供人员、专页和数据表链接")
        self._note(
            c1,
            "配置表默认按列填写人员、类型、页面编码和数据表链接。"
            "标记为有效的行才会汇总。数据表链接可以是蓝字超链接，程序会打开里面的流量工作表。",
        )
        self.var_roster_config_url = tk.StringVar()
        self.var_roster_config_sheet = tk.StringVar()
        self.var_roster_start_row = tk.StringVar(value="2")
        self._entry(c1, "配置表链接", self.var_roster_config_url)
        g = self._row3(c1)
        self._cell(g, 0, "工作表名（留空=第一个）", self.var_roster_config_sheet)
        self._cell(g, 1, "数据起始行", self.var_roster_start_row)

        c2 = self._card(p, "2. 字段映射", "列字母可改")
        self._note(c2, "每一行：字段名、用途、列字母。可按源表实际列修改。")
        fh = tk.Frame(c2, bg="#eff6ff")
        fh.pack(fill="x")
        tk.Label(fh, text="字段", bg="#eff6ff", font=FS, fg=C["muted"], width=16, anchor="w").pack(side="left", padx=4, pady=4)
        tk.Label(fh, text="用途", bg="#eff6ff", font=FS, fg=C["muted"], width=14, anchor="w").pack(side="left", padx=4, pady=4)
        tk.Label(fh, text="列", bg="#eff6ff", font=FS, fg=C["muted"], width=8, anchor="w").pack(side="left", padx=4, pady=4)
        self.roster_map_box = tk.Frame(c2, bg=C["card"])
        self.roster_map_box.pack(fill="x")
        self._roster_map_rows: list[tk.Frame] = []
        hr = tk.Frame(c2, bg=C["card"])
        hr.pack(fill="x", pady=4)
        StyleBtn(hr, "ghost", text="+ 添加字段", command=lambda: self._add_roster_map_row("", "name", "")).pack(side="right")
        StyleBtn(hr, "ghost", text="恢复默认列", command=self._reset_roster_map).pack(side="right", padx=6)
        for item in DEFAULT_COLUMNS:
            self._add_roster_map_row(item["field"], item["role"], item["column"])

        c3 = self._card(p, "3. 写入目标表", "按队别分工作表")
        self._note(
            c3,
            "目标表没有固定 sheet 名：按 A 列队别自动建工作表，同一队别写进同名 sheet。"
            "一列一个专页编码，同一个名字的专页排在一起。"
            "第 1 行 chat，第 2 行数据表链接，第 3 行专页链接，第 4 行空，第 5 行专页编码，第 6 行名字，第 7 行空，第 8 行类型，9–23 行空。"
            "A24 起按流量表结构：一行日期，下面 24 个小时段（00:00-01:00 … 23:00-00:00），再下一日期，日期新的在前。"
            "用第 5 行页面编码去流量表找对应列，按 A 列日期/时段对齐写入。",
        )
        self.var_roster_target_url = tk.StringVar()
        self.var_roster_traffic_sheet = tk.StringVar(value="流量")
        self.var_roster_date_start = tk.StringVar(value="24")
        self._entry(c3, "目标表格链接", self.var_roster_target_url)
        g = self._row3(c3)
        self._cell(g, 0, "流量工作表名", self.var_roster_traffic_sheet)
        self._cell(g, 1, "日期起始行", self.var_roster_date_start)

    def _add_roster_map_row(self, field: str, role: str, column: str) -> None:
        row = tk.Frame(self.roster_map_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        f = self._input(row, font=MONO, width=16)
        f.insert(0, field)
        f.pack(side="left", ipady=4, padx=(0, 6))
        r = self._input(row, font=MONO, width=14)
        r.insert(0, role)
        r.pack(side="left", ipady=4, padx=(0, 6))
        c = self._input(row, font=MONO, width=8)
        c.insert(0, column)
        c.pack(side="left", ipady=4, padx=(0, 6))
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_roster_map_row(row)).pack(side="left")
        row._field, row._role, row._column = f, r, c
        self._roster_map_rows.append(row)

    def _del_roster_map_row(self, row) -> None:
        if row in self._roster_map_rows:
            self._roster_map_rows.remove(row)
        row.destroy()
        if not self._roster_map_rows:
            self._reset_roster_map()

    def _reset_roster_map(self) -> None:
        from roster_fill import DEFAULT_COLUMNS

        for row in list(self._roster_map_rows):
            row.destroy()
        self._roster_map_rows = []
        for item in DEFAULT_COLUMNS:
            self._add_roster_map_row(item["field"], item["role"], item["column"])

    def _read_roster_columns(self) -> list[dict]:
        out = []
        for row in self._roster_map_rows:
            role = row._role.get().strip().lower()
            column = row._column.get().strip().upper()
            if role and column:
                out.append({"field": row._field.get().strip() or role, "role": role, "column": column})
        return out

    def _set_roster_columns(self, items) -> None:
        from roster_fill import DEFAULT_COLUMNS

        for row in list(self._roster_map_rows):
            row.destroy()
        self._roster_map_rows = []
        columns = [item for item in (items or []) if isinstance(item, dict) and item.get("column")]
        if not columns:
            columns = list(DEFAULT_COLUMNS)
        else:
            seen = {str(item.get("role") or "").strip().lower() for item in columns}
            for item in DEFAULT_COLUMNS:
                if item["role"] not in seen:
                    columns.append(item)
        for item in columns:
            self._add_roster_map_row(str(item.get("field") or ""), str(item.get("role") or ""), str(item.get("column") or ""))

    def _build_log(self) -> None:
        logf = tk.Frame(self, bg=C["log"])
        logf.pack(fill="x", side="bottom")
        top = tk.Frame(logf, bg=C["log"])
        top.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(top, text="运行日志", bg=C["log"], fg=C["cream"], font=FB).pack(side="left")
        self.badge = tk.Label(top, text="待命", bg="#334155", fg=C["cream"], font=FS, padx=8, pady=2)
        self.badge.pack(side="right")
        self.log = tk.Text(logf, bg=C["log"], fg="#dce7f5", insertbackground="#dce7f5", font=MONO, wrap="word", height=8, bd=0)
        self.log.pack(fill="x", padx=10, pady=8)
        self.log.insert("end", "软件已启动。左侧可切换、重命名或新增配置菜单；改完点保存。\n")
        self.log.configure(state="disabled")

    def _sa_emails(self) -> list[str]:
        emails: list[str] = []
        for path in self._cred_files or []:
            emails.append(service_account_email(Path(path)) or Path(path).name)
        return emails

    def _copy_sa(self):
        text = ""
        box = getattr(self, "_settings_list", None)
        if box is not None:
            try:
                sel = box.curselection()
                if sel:
                    text = str(box.get(sel[0]) or "")
                    if ". " in text:
                        text = text.split(". ", 1)[-1]
            except Exception:
                text = ""
        if not text:
            text = self.var_sa.get()
        if not text or text in ("未找到服务账号", "还没有服务账号，请点击「添加服务账号」"):
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._append_log("已复制服务账号邮箱")

    def _refresh_sa_display(self) -> None:
        files = normalize_credential_paths(self._cred_files)
        self._cred_files = files
        self._sa_files_shown = list(files)
        self.var_credentials.set(files[0] if files else "")
        emails = self._sa_emails()
        self.var_sa.set("\n".join(emails) if emails else "未找到服务账号")
        if len(files) > 1:
            self.sa_hint.set(f"{len(files)} 个服务账号轮询 · 每个都要共享编辑者")
        else:
            self.sa_hint.set("源表和目标表都要共享给它 · 编辑者")
        self._refresh_settings_list(emails)

    def _refresh_settings_list(self, emails: list[str] | None = None) -> None:
        box = getattr(self, "_settings_list", None)
        if box is None:
            return
        try:
            if not box.winfo_exists():
                return
        except Exception:
            return
        if emails is None:
            emails = self._sa_emails()
        box.delete(0, "end")
        if emails:
            for index, email in enumerate(emails, 1):
                box.insert("end", f"{index}. {email}")
        else:
            box.insert("end", "还没有服务账号，请点击「添加服务账号」")

    def _load_global_credentials(self) -> None:
        files = discover_credential_files(self.cfg, self._menus)
        self._cred_files = files
        self._refresh_sa_display()
        top = discover_credential_files(self.cfg, None, include_copied=False)
        if files and files != top:
            self._persist_credentials()

    def _apply_creds_to_cfg(self, cfg):
        files = list(self._cred_files or [])
        if files:
            cfg.credentials_files = files
            cfg.credentials_file = files[0]
        return cfg

    def _persist_credentials(self) -> None:
        files = list(self._cred_files or [])
        first = files[0] if files else ""
        for item in self._menus:
            settings = item.setdefault("settings", {})
            settings["credentials_files"] = list(files)
            settings["credentials_file"] = first
        try:
            cfg = load_config()
            cfg.credentials_files = list(files)
            cfg.credentials_file = first
            for item in cfg.ui_menus or []:
                if not isinstance(item, dict):
                    continue
                settings = item.setdefault("settings", {})
                if isinstance(settings, dict):
                    settings["credentials_files"] = list(files)
                    settings["credentials_file"] = first
            save_config(cfg)
            self.cfg.credentials_files = list(files)
            self.cfg.credentials_file = first
            if getattr(self.cfg, "ui_menus", None):
                self.cfg.ui_menus = cfg.ui_menus
        except Exception as exc:
            self._append_log(f"保存服务账号失败：{exc}")

    def _open_config_transfer(self) -> None:
        from config_transfer_dialog import ConfigTransferDialog
        existing=getattr(self,'_transfer_win',None)
        if existing is not None and existing.winfo_exists():
            existing.lift();return
        self._transfer_win=ConfigTransferDialog(self)

    def _open_settings(self) -> None:
        existing = getattr(self, "_settings_win", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    self._refresh_settings_list()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self)
        win.title("设置")
        win.configure(bg=C["paper"])
        win.transient(self)
        win.minsize(640, 720)
        win.geometry(f"700x780+{self.winfo_rootx() + 60}+{self.winfo_rooty() + 20}")
        self._settings_win = win

        head = tk.Frame(win, bg=C["head"])
        head.pack(fill="x")
        tk.Label(head, text="设置", bg=C["head"], fg=C["cream"], font=FH).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(
            head,
            text="服务账号给表格用。迁移请用顶部配置导入 / 导出；已有图库复用原站点。",
            bg=C["head"],
            fg="#cbd5e1",
            font=FS,
        ).pack(anchor="w", padx=18, pady=(0, 12))

        card = tk.Frame(win, bg=C["card"], highlightbackground=C["line"], highlightthickness=1)
        card.pack(fill="both", expand=True, padx=16, pady=16)
        tk.Label(card, text="服务账号", bg=C["card"], fg=C["ink"], font=FB).pack(anchor="w", padx=14, pady=(12, 4))
        tk.Label(card, textvariable=self.sa_hint, bg=C["card"], fg=C["muted"], font=FS, wraplength=540, justify="left").pack(
            anchor="w", padx=14, pady=(0, 8)
        )
        list_wrap = tk.Frame(card, bg=C["card"])
        list_wrap.pack(fill="both", expand=True, padx=14)
        box = tk.Listbox(
            list_wrap,
            font=MONO,
            relief="solid",
            bd=1,
            activestyle="dotbox",
            selectmode="browse",
            bg="#f8fafc",
            fg=C["ink"],
            highlightthickness=0,
            height=6,
        )
        bar = ttk.Scrollbar(list_wrap, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=bar.set)
        box.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self._settings_list = box
        self._refresh_settings_list()

        def _on_close_settings():
            self._settings_win = None
            self._settings_list = None
            try:
                win.destroy()
            except Exception:
                pass

        actions = tk.Frame(card, bg=C["card"])
        actions.pack(fill="x", padx=14, pady=12)
        StyleBtn(actions, "ghost", text="+ 添加服务账号", command=self._choose_credentials).pack(side="left", padx=(0, 6))
        StyleBtn(actions, "ghost", text="复制邮箱", command=self._copy_sa).pack(side="left", padx=(0, 6))
        StyleBtn(actions, "ghost", text="移除末个", command=self._remove_last_credential).pack(side="left")

        gallery = tk.Frame(win, bg=C["card"], highlightbackground=C["line"], highlightthickness=1)
        gallery.pack(fill="x", padx=16, pady=(0, 16))
        tk.Label(gallery, text="图库站点（Cloudflare）", bg=C["card"], fg=C["ink"], font=FB).pack(anchor="w", padx=14, pady=(12, 4))
        tk.Label(
            gallery,
            text="部署结果只写入左侧当前选中的模板第 4 步。要给新模板单独一座站：先新增并点选该模板，再换域名部署。换回旧域名后点「填入此域名的密钥」，会把以前缓存的地址和密钥写回当前模板。旧模板不会被改掉。",
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=640,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 8))
        self.var_cf_login = tk.StringVar(value="尚未登录 Cloudflare")
        tk.Label(gallery, textvariable=self.var_cf_login, bg=C["card"], fg=C["head"], font=FS).pack(anchor="w", padx=14)
        login_row = tk.Frame(gallery, bg=C["card"])
        login_row.pack(fill="x", padx=14, pady=(6, 4))
        StyleBtn(login_row, "ghost", text="1. 登录 Cloudflare", command=self._cf_login).pack(side="left")
        StyleBtn(login_row, "ghost", text="多账号与发布范围", command=self._manage_cf_accounts).pack(side="left", padx=8)
        self.var_cf_host = tk.StringVar()
        self._entry(gallery, "2. 网站域名（例如 gallery.example.com）", self.var_cf_host)
        self.var_cf_access_password = tk.StringVar()
        self._entry(
            gallery,
            "访问密码（修改 / 重置请打开“多账号与发布范围”中的独立密码按钮）",
            self.var_cf_access_password,
            show="•",
        )
        deploy_row = tk.Frame(gallery, bg=C["card"])
        deploy_row.pack(fill="x", padx=14, pady=(4, 8))
        self.btn_cf_deploy = StyleBtn(deploy_row, "primary", text="3. 管理 / 连接站点", command=self._manage_cf_accounts)
        self.btn_cf_deploy.pack(side="left")
        StyleBtn(deploy_row, "ghost", text="填入此域名的密钥", command=self._restore_cf_site_for_host).pack(
            side="left", padx=(8, 0)
        )
        StyleBtn(deploy_row, "ghost", text="关闭", command=_on_close_settings).pack(side="right")
        self.var_cf_deploy_status = tk.StringVar(value="")
        tk.Label(
            gallery,
            textvariable=self.var_cf_deploy_status,
            bg=C["card"],
            fg=C["muted"],
            font=FS,
            wraplength=640,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 12))
        self._refresh_cf_login_status()
        win.protocol("WM_DELETE_WINDOW", _on_close_settings)

    def _refresh_cf_login_status(self) -> None:
        state = load_cf_state()
        if not getattr(self, "var_cf_login", None):
            return
        if state.get("api_token") and state.get("account_id"):
            name = state.get("account_name") or state.get("account_id")
            self.var_cf_login.set(f"已登录：{name}")
        else:
            self.var_cf_login.set("尚未登录 Cloudflare")
        host = state.get("host") or ""
        if host and hasattr(self, "var_cf_host") and not self.var_cf_host.get().strip():
            self.var_cf_host.set(host)
        if hasattr(self, "var_cf_access_password") and not self.var_cf_access_password.get():
            self.var_cf_access_password.set(str(state.get("access_password") or ""))
        remembered = known_gallery_sites(state)
        if remembered and hasattr(self, "var_cf_deploy_status") and not self.var_cf_deploy_status.get():
            hosts = "、".join(item.get("host") or "" for item in remembered if item.get("host"))
            self.var_cf_deploy_status.set(f"已记住的站点：{hosts}。当前会写入模板「{self._active_menu_name()}」。")

    def _active_menu_name(self) -> str:
        item = next((entry for entry in self._menus if entry.get("id") == self._active_menu_id), None)
        return str((item or {}).get("name") or "当前模板")

    def _lookup_cf_site(self, host: str) -> dict:
        host = normalize_host(host)
        if not host:
            return {}
        return next((item for item in known_gallery_sites(load_cf_state()) if item.get("host") == host), {}) or {}

    def _restore_cf_site_for_host(self) -> None:
        host = ""
        if hasattr(self, "var_cf_host"):
            host = self.var_cf_host.get()
        site = self._lookup_cf_site(host)
        if not site:
            messagebox.showinfo(
                "数据汇总工具",
                "这个域名还没有部署记录。先点「生成密钥并部署」会新建站点，并写入当前选中的模板。",
            )
            return
        url = str(site.get("publish_url") or "").strip() or f"https://{site.get('host')}/api/publish-cache"
        secret = str(site.get("secret") or "").strip()
        self._persist_cf_publish(url, secret)
        name = self._active_menu_name()
        msg = f"已把 {site.get('host')} 的发布地址和密钥写回模板「{name}」第 4 步。"
        if hasattr(self, "var_cf_deploy_status"):
            self.var_cf_deploy_status.set(msg)
        self._append_log(msg)

    def _cf_login(self) -> None:
        parent = self._settings_win or self
        webbrowser.open(TOKEN_CREATE_URL)
        dlg = tk.Toplevel(parent)
        dlg.title("登录 Cloudflare")
        dlg.configure(bg=C["paper"])
        dlg.transient(parent)
        dlg.geometry(f"+{parent.winfo_rootx() + 40}+{parent.winfo_rooty() + 80}")
        tk.Label(
            dlg,
            text="浏览器会打开 Cloudflare 令牌页（权限已预填）。\n确认包含：Pages 编辑、R2 编辑、Workers Scripts 编辑、\nDNS 编辑、Account 读取；账号选全部、区域选全部。\n点 Create Token 后，把 API Token 粘贴到下面（不要粘 Global API Key）。",
            bg=C["paper"],
            fg=C["ink"],
            font=FS,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(16, 8))
        var_token = tk.StringVar()
        entry = self._input(dlg, font=MONO, textvariable=var_token, show="•", width=48)
        entry.pack(fill="x", padx=18, ipady=6)
        entry.focus_set()
        var_account = tk.StringVar()
        tk.Label(dlg, text='选择账号（先读取令牌可访问的账号）', bg=C['paper'], fg=C['ink'], font=FS).pack(anchor='w', padx=18, pady=(10,3))
        account_combo = ttk.Combobox(dlg, textvariable=var_account, state='readonly')
        account_combo.pack(fill='x', padx=18)
        status = tk.StringVar(value="")
        tk.Label(dlg, textvariable=status, bg=C["paper"], fg=C["muted"], font=FS, wraplength=680, justify='left').pack(anchor="w", padx=18, pady=6)
        pending = {'token': '', 'accounts': {}}
        menu_id = self._active_menu_id

        def on_main(callback):
            self.after(0, lambda: callback() if dlg.winfo_exists() else None)

        def failed(error):
            status.set(error)
            login_button.configure(state='normal')
            self._append_log('Cloudflare 登录检查：' + error)

        def logged_in(state):
            self._refresh_cf_login_status()
            self._append_log(f"已登录 Cloudflare：{state.get('account_name') or state.get('account_id')}")
            dlg.destroy()

        def finish_login(token, identity):
            status.set('正在登录所选账号…')
            login_button.configure(state='disabled')
            def work():
                try:
                    state = login_with_token(token, account_id=identity, allowed_menu_ids=[menu_id])
                    on_main(lambda: logged_in(state))
                except Exception as exc:
                    on_main(lambda error=str(exc): failed(error))
            threading.Thread(target=work, daemon=True).start()

        def scanned(token, accounts):
            if var_token.get().strip() != token:
                status.set('令牌已更改，请重新读取账号')
                login_button.configure(state='normal')
                return
            if not accounts:
                failed('当前令牌读不到任何账号，请核对 Account Settings Read 和账号资源范围')
                return
            pending['token'] = token
            pending['accounts'] = {f"{row['name']} · {row['id'][-8:]}": row['id'] for row in accounts}
            account_combo.configure(values=list(pending['accounts']))
            previous = load_cf_state().get('account_id')
            var_account.set(next((name for name,identity in pending['accounts'].items() if identity==previous), next(iter(pending['accounts']))))
            login_button.configure(state='normal', text='登录所选账号')
            if len(accounts) == 1:
                finish_login(token, accounts[0]['id'])
            else:
                status.set(f'识别到 {len(accounts)} 个账号，请选择实际要使用的账号，再点“登录所选账号”。登录不会检查或开通 R2。')

        def save_token():
            token = var_token.get().strip()
            if not token:
                status.set("请粘贴令牌")
                return
            if pending['token'] == token and pending['accounts']:
                identity = pending['accounts'].get(var_account.get())
                if identity:
                    finish_login(token, identity)
                else:
                    status.set('请先选择账号')
                return
            login_button.configure(state='disabled')
            status.set('正在读取账号…')
            def work():
                try:
                    accounts = CloudflareAPI(token).list_accounts()
                    on_main(lambda: scanned(token, accounts))
                except Exception as exc:
                    on_main(lambda error=str(exc): failed(error))
            threading.Thread(target=work, daemon=True).start()

        btns = tk.Frame(dlg, bg=C["paper"])
        btns.pack(fill="x", padx=18, pady=(4, 16))
        login_button = StyleBtn(btns, "primary", text="读取账号并登录", command=save_token)
        login_button.pack(side="left")
        StyleBtn(btns, "ghost", text="取消", command=dlg.destroy).pack(side="left", padx=8)
        dlg.grab_set()

    def _persist_cf_publish(self, url: str, secret: str) -> None:
        url = (url or "").strip()
        secret = (secret or "").strip()
        self.var_cf_url.set(url)
        self.var_cf_secret.set(secret)
        try:
            cfg = load_config()
            cfg.cf_publish_url = url
            cfg.cf_publish_secret = secret
            active = self._active_menu_id
            for collection in (cfg.ui_menus or [], self._menus):
                for item in collection:
                    if not isinstance(item, dict) or item.get("id") != active:
                        continue
                    settings = item.setdefault("settings", {})
                    if isinstance(settings, dict):
                        settings["cf_publish_url"] = url
                        settings["cf_publish_secret"] = secret
            save_config(cfg)
            self.cfg.cf_publish_url = url
            self.cfg.cf_publish_secret = secret
        except Exception as exc:
            self._append_log(f"保存发布地址失败：{exc}")

    def _cf_deploy(self) -> None:
        host = normalize_host(self.var_cf_host.get() if hasattr(self, "var_cf_host") else "")
        if not host:
            messagebox.showinfo("数据汇总工具", "请先填写网站域名，例如 gallery.example.com")
            return
        state = load_cf_state()
        if not state.get("api_token"):
            messagebox.showinfo("数据汇总工具", "请先点「登录 Cloudflare」")
            return
        if getattr(self, "btn_cf_deploy", None):
            self.btn_cf_deploy.configure(state="disabled", text="部署中…")
        self.var_cf_deploy_status.set("正在部署，请稍候…")

        def work():
            lines: list[str] = []

            def log(message: str) -> None:
                lines.append(str(message))
                self.after(0, lambda m=str(message): self._append_log(m))
                self.after(0, lambda m=str(message): self.var_cf_deploy_status.set(m))

            try:
                password = ""
                if hasattr(self, "var_cf_access_password"):
                    password = self.var_cf_access_password.get().strip()
                result = deploy_gallery(host, access_password=password, log=log)
                self.after(0, lambda: self._persist_cf_publish(result["publish_url"], result["secret"]))
                menu_name = self._active_menu_name()
                self.after(
                    0,
                    lambda: self.var_cf_deploy_status.set(
                        f"完成。站点 {result['site_url']} 已写入模板「{menu_name}」第 4 步。"
                    ),
                )
            except CloudflareError as exc:
                self.after(0, lambda error=str(exc): self.var_cf_deploy_status.set(error))
                self.after(0, lambda error=str(exc): self._append_log(error))
            except Exception as exc:
                self.after(0, lambda error=str(exc): self.var_cf_deploy_status.set(f"部署失败：{error}"))
                self.after(0, lambda error=str(exc): self._append_log(f"部署失败：{error}"))
            finally:
                self.after(0, lambda: self.btn_cf_deploy.configure(state="normal", text="3. 生成密钥并部署"))

        threading.Thread(target=work, daemon=True).start()

    def _remove_last_credential(self) -> None:
        if len(self._cred_files) <= 1:
            messagebox.showinfo("数据汇总工具", "至少保留一个服务账号。")
            return
        removed = self._cred_files.pop()
        self._refresh_sa_display()
        self._persist_credentials()
        self._append_log(f"已移除服务账号：{Path(removed).name}")

    def _choose_credentials(self) -> None:
        current = Path(self.var_credentials.get().strip()) if self.var_credentials.get().strip() else None
        initial = current.parent if current and current.parent.exists() else Path.home()
        selected = filedialog.askopenfilenames(
            title="选择 Google 服务账号 JSON（可多选）",
            initialdir=str(initial),
            filetypes=(("JSON 文件", "*.json"), ("所有文件", "*.*")),
            parent=self._settings_win if getattr(self, "_settings_win", None) else self,
        )
        if not selected:
            return
        added = []
        existing = {str(Path(p).resolve()) for p in self._cred_files if Path(p).exists()}
        for item in selected:
            src = Path(item)
            email = service_account_email(src)
            if not email:
                messagebox.showerror("数据汇总工具", f"{src.name} 不是有效的 Google 服务账号 JSON（未找到 client_email）。")
                continue
            slug = email.split("@")[0].replace(".", "-")
            dest = SCRIPT_DIR / f"credentials-{slug}.json"
            try:
                shutil.copy2(src, dest)
                stored = str(dest.resolve())
            except Exception:
                stored = str(src)
            key = str(Path(stored).resolve()) if Path(stored).exists() else stored
            if key not in existing and stored not in self._cred_files:
                self._cred_files.append(stored)
                existing.add(key)
                added.append(email)
        self._refresh_sa_display()
        self._persist_credentials()
        if added:
            self._append_log("已添加服务账号：" + "、".join(added) + "。额度满了会自动换下一个，不必长时间等待。")

    def _check_updates(self) -> None:
        self.btn_update.configure(state="disabled", text="检查中…")

        def work():
            try:
                import json
                from urllib.request import Request, urlopen

                req = Request(
                    UPDATE_API_URL,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": f"sheets-post-filter/{APP_VERSION}",
                    },
                )
                from secure_http import create_ssl_context
                with urlopen(req, timeout=15, context=create_ssl_context()) as response:
                    data = json.loads(response.read().decode("utf-8"))
                latest = str(data.get("tag_name") or data.get("name") or "").strip()
                url = str(data.get("html_url") or RELEASES_URL)
                if not latest:
                    raise RuntimeError("更新服务器没有返回版本号")
                self.after(0, lambda: self._show_update_result(latest, url))
            except Exception as exc:
                self.after(0, lambda e=str(exc): self._show_update_error(e))

        threading.Thread(target=work, daemon=True).start()

    def _show_update_result(self, latest: str, url: str) -> None:
        self.btn_update.configure(state="normal", text="检查更新")
        extra = "\n\n点「打开日志」可查看报错记录。"
        if version_tuple(latest) > version_tuple(APP_VERSION):
            if messagebox.askyesno(
                "发现新版本",
                f"当前版本：{APP_VERSION}\n最新版本：{latest}\n\n是否打开下载页面？{extra}",
            ):
                import webbrowser

                webbrowser.open(url)
        else:
            if messagebox.askyesno("检查更新", f"当前版本 {APP_VERSION} 已是最新。\n\n是否打开运行日志？"):
                self._open_logs()

    def _show_update_error(self, error: str) -> None:
        self.btn_update.configure(state="normal", text="检查更新")
        if messagebox.askyesno(
            "检查更新失败",
            "无法读取 GitHub Release。\n\n" + error + "\n\n是否打开运行日志排查？",
        ):
            self._open_logs()

    def _open_logs(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(LOG_DIR.glob("app-*.log"), reverse=True)
        latest = files[0] if files else None
        win = tk.Toplevel(self)
        win.title("运行日志")
        win.geometry("780x480")
        win.configure(bg=C["paper"])
        tk.Label(win, text=str(latest or LOG_DIR), bg=C["paper"], fg=C["muted"], font=FS).pack(anchor="w", padx=12, pady=8)
        text = tk.Text(win, bg=C["log"], fg="#dce7f5", font=MONO, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        if latest and latest.exists():
            content = latest.read_text(encoding="utf-8", errors="replace")
            text.insert("end", content[-40000:])
        else:
            text.insert("end", "还没有日志文件。任务报错后会写到这个文件夹。")
        text.configure(state="disabled")
        btns = tk.Frame(win, bg=C["paper"])
        btns.pack(fill="x", padx=12, pady=8)
        StyleBtn(btns, "ghost", text="打开日志文件夹", command=lambda: _open_folder(LOG_DIR)).pack(side="left")
        StyleBtn(btns, "primary", text="关闭", command=win.destroy).pack(side="right")

    def _set_interval(self, minutes: int) -> None:
        self.var_minutes.set(str(minutes))
        self.var_sched.set(True)
        self._save(quiet=True)
        self._append_log(f"定时间隔已改为 {minutes} 分钟，并已启用")

    def _upd_align_headers(self):
        n = sum(1 for row in getattr(self, "_align_map_rows", []) if row._target.get().strip())
        if hasattr(self, "align_header_count"):
            self.align_header_count.configure(text=f"{n} 列")

    def _add_align_map_row(self, target: str = "", source: str = "") -> None:
        row = tk.Frame(self.align_map_box, bg=C["card"])
        row.pack(fill="x", pady=3)
        t = self._input(row, font=MONO, width=18)
        t.insert(0, target)
        t.pack(side="left", ipady=4, padx=(0, 6))
        s = self._input(row, font=MONO, width=18)
        s.insert(0, source or target)
        s.pack(side="left", ipady=4, padx=(0, 6))
        StyleBtn(row, "ghost", text="删除", command=lambda: self._del_align_map_row(row)).pack(side="left")
        row._target, row._source = t, s
        t.bind("<KeyRelease>", lambda _e: self._upd_align_headers())
        self._align_map_rows.append(row)
        self._upd_align_headers()

    def _del_align_map_row(self, row) -> None:
        if row in self._align_map_rows:
            self._align_map_rows.remove(row)
        row.destroy()
        if not self._align_map_rows:
            self._add_align_map_row("", "")
        self._upd_align_headers()

    def _read_align_mappings(self) -> list[dict]:
        out = []
        for row in getattr(self, "_align_map_rows", []):
            target = row._target.get().strip()
            if not target:
                continue
            source = row._source.get().strip() or target
            out.append({"target": target, "source": source})
        return out

    def _write_align_mappings(self, mappings) -> None:
        for row in list(getattr(self, "_align_map_rows", [])):
            row.destroy()
        self._align_map_rows = []
        cleaned = [item for item in (mappings or []) if isinstance(item, dict) and str(item.get("target") or "").strip()]
        if not cleaned:
            self._add_align_map_row("", "")
            return
        for item in cleaned:
            self._add_align_map_row(str(item.get("target") or "").strip(), str(item.get("source") or item.get("target") or "").strip())

    def _refresh_align_profiles(self) -> None:
        self._save_align_profile()
        values = ["所有链接的默认映射"]
        self._align_profile_urls = {"所有链接的默认映射": "__default__"}
        for index, source in enumerate(self._read_src("_align_rows", align=True), 1):
            label = f"单独配置：{source.get('sheet') or f'链接 {index}'}"
            values.append(label)
            self._align_profile_urls[label] = source["url"]
        self.align_profile_combo.configure(values=values)
        selected = next((label for label, key in self._align_profile_urls.items() if key == self._align_profile_key), values[0])
        self.var_align_profile.set(selected)

    def _save_align_profile(self) -> None:
        if not hasattr(self, "align_map_box"):
            return
        mappings = self._read_align_mappings()
        if self._align_profile_key == "__default__":
            self._align_default_mappings = mappings
        else:
            self._align_profiles[self._align_profile_key] = mappings

    def _switch_align_profile(self) -> None:
        self._save_align_profile()
        key = getattr(self, "_align_profile_urls", {}).get(self.var_align_profile.get(), "__default__")
        self._align_profile_key = key
        mappings = self._align_default_mappings if key == "__default__" else self._align_profiles.get(key, self._align_default_mappings)
        self._write_align_mappings(mappings)

    def _set_str(self, var: tk.StringVar, value, default: str = "") -> None:
        text = "" if value is None else str(value)
        if text == "" and default:
            text = default
        if var.get() != text:
            var.set(text)

    def _set_bool(self, var: tk.BooleanVar, value, default: bool = False) -> None:
        flag = default if value is None else bool(value)
        if var.get() != flag:
            var.set(flag)

    def _settings_slice(self, template: str) -> dict:
        creds = list(self._cred_files or ([self.var_credentials.get().strip()] if self.var_credentials.get().strip() else []))
        shared = {
            "credentials_file": (creds[0] if creds else self.var_credentials.get().strip()),
            "credentials_files": creds,
            "cf_profile_id": getattr(self, '_filter_cf_profile_id', ''),
        }
        if template == "worksheet_merge":
            return {**shared, **self._wm_settings_from_ui()}
        if template == "catalog":
            return {
                **shared,
                "catalog_index_url": self.var_catalog_index_url.get().strip(),
                "catalog_index_sheet": self.var_catalog_index_sheet.get().strip(),
                "catalog_start_row": self.var_catalog_start_row.get().strip(),
                "catalog_url_col": self.var_catalog_url_col.get().strip(),
                "catalog_sheet_col": self.var_catalog_sheet_col.get().strip(),
                "catalog_target_url": self.var_catalog_target_url.get().strip(),
                "catalog_output_sheet": self.var_catalog_output_sheet.get().strip(),
                "catalog_output_start_row": self.var_catalog_output_start_row.get().strip(),
                "catalog_dedupe_col": self.var_catalog_dedupe_col.get().strip(),
                "catalog_keep_each_header": self.var_catalog_keep_header.get(),
                "catalog_add_source": self.var_catalog_add_source.get(),
                "catalog_skip_existing": self.var_catalog_skip_existing.get(),
                "catalog_date_filter_enabled": self.var_catalog_date_filter.get(),
                "catalog_date_col": self.var_catalog_date_col.get().strip(),
                "catalog_start_date": self.var_catalog_start.get().strip(),
                "catalog_end_date": self.var_catalog_end.get().strip(),
                "catalog_exclude_sheets": self._read_catalog_exclude(),
                "catalog_schedule_enabled": self.var_catalog_sched.get(),
                "catalog_schedule_minutes": self.var_catalog_minutes.get().strip(),
            }
        if template == "posts":
            return {
                **shared,
                **self._posts_settings_from_ui(),
            }
        if template == "align":
            self._save_align_profile()
            return {
                **shared,
                "align_target_url": self.var_align_target_url.get().strip(),
                "align_output_sheet": self.var_align_output_sheet.get().strip(),
                "align_start_row": self.var_align_start_row.get().strip(),
                "align_source_sheet": self.var_align_source_sheet.get().strip(),
                "align_header_row": self.var_align_header_row.get().strip(),
                "align_schedule_minutes": self.var_align_minutes.get().strip(),
                "align_include_headers": self.var_align_include_headers.get(),
                "align_schedule_enabled": self.var_align_sched.get(),
                "align_schedule_only_if_changed": self.var_align_changed.get(),
                "align_sources": self._read_src("_align_rows", align=True),
                "align_headers": [item["target"] for item in self._align_default_mappings],
                "align_mappings": self._align_default_mappings,
                "align_mapping_profiles": self._align_profiles,
            }
        if template == "sheet_metrics":
            return {
                **shared,
                "sm_source_url": self.var_sm_source_url.get().strip(),
                "sm_source_mode": "single" if self.var_sm_source_mode.get() == "只扫描指定工作表" else "all",
                "sm_source_sheet": self.var_sm_source_sheet.get().strip(),
                "sm_exclude_exact": [x.strip() for x in re.split(r"[,，;；]+", self.var_sm_exclude_exact.get()) if x.strip()],
                "sm_exclude_contains": [x.strip() for x in re.split(r"[,，;；]+", self.var_sm_exclude_contains.get()) if x.strip()],
                "sm_source_code_row": self.var_sm_source_code_row.get().strip(),
                "sm_source_name_row": self.var_sm_source_name_row.get().strip(),
                "sm_source_date_col": self.var_sm_source_date_col.get().strip(),
                "sm_source_data_start_row": self.var_sm_source_data_start_row.get().strip(),
                "sm_source_metric_start_col": self.var_sm_source_metric_start_col.get().strip(),
                "sm_source_group_width": self.var_sm_source_group_width.get().strip(),
                "sm_source_metric_count": self.var_sm_source_metric_count.get().strip(),
                "sm_target_url": self.var_sm_target_url.get().strip(),
                "sm_target_sheet": self.var_sm_target_sheet.get().strip(),
                "sm_target_name_col": self.var_sm_target_name_col.get().strip(),
                "sm_target_code_col": self.var_sm_target_code_col.get().strip(),
                "sm_target_date_row": self.var_sm_target_date_row.get().strip(),
                "sm_target_data_start_row": self.var_sm_target_data_start_row.get().strip(),
                "sm_target_group_start_col": self.var_sm_target_group_start_col.get().strip(),
                "sm_target_group_width": self.var_sm_target_group_width.get().strip(),
                "sm_target_write_count": self.var_sm_target_write_count.get().strip(),
                "sm_date_mode": "full_date" if self.var_sm_date_mode.get() == "按完整年月日匹配" else "month_day",
                "sm_batch_size": self.var_sm_batch_size.get().strip(),
            }
        if template in ("video", "custom"):
            return {
                **shared,
                "vd_source_url": self.var_vd_source_url.get().strip(),
                "vd_source_sheet": self.var_vd_source_sheet.get().strip(),
                "vd_start_row": self.var_vd_start_row.get().strip(),
                "vd_col_date": self.var_vd_col_date.get().strip(),
                "vd_col_link": self.var_vd_col_link.get().strip(),
                "vd_col_name": self.var_vd_col_name.get().strip(),
                "vd_col_type": self.var_vd_col_type.get().strip(),
                "vd_types": self._read_vd_types(),
                "vd_report_categories": self._read_vd_report_cats(),
                "vd_duration_rules": self._read_vd_duration_rules(),
                "vd_dest_url": self.var_vd_dest_url.get().strip(),
                "vd_log_sheet": self.var_vd_log_sheet.get().strip(),
                "vd_report_sheet": self.var_vd_report_sheet.get().strip(),
                "vd_out_start_row": self.var_vd_out_start_row.get().strip(),
                "vd_unit_seconds": self.var_vd_unit.get().strip(),
                "vd_count_mode": (
                    {"逐条视频按30秒计数": "per_video_ceil", "按时长区间统计数量": "duration_buckets"}.get(
                        self.var_vd_count_mode.get(), "divide_total"
                    )
                ),
                "vd_include_headers": self.var_vd_include_headers.get(),
                "vd_schedule_enabled": self.var_vd_schedule_enabled.get(),
                "vd_schedule_minutes": self.var_vd_schedule_minutes.get().strip(),
                "vd_start_date": self.var_vd_start.get().strip(),
                "vd_end_date": self.var_vd_end.get().strip(),
                "vd_source_sheets": [value.strip() for value in self.var_vd_source_sheet.get().replace("，", ",").split(",") if value.strip()],
                "vd_date_filter_enabled": self.var_vd_date_filter_enabled.get(),
                "vd_type_filter_mode": {"只包含这些类型": "include", "排除这些类型": "exclude", "不筛选类型": "all"}.get(self.var_vd_type_filter_mode.get(), "include"),
                "vd_write_log": template == "video",
                "vd_other_category": self.var_vd_other_category.get().strip(),
                "vd_exclude_types": self._read_vd_exclude_types(),
                "vd_category_mode": (
                    "other_only"
                    if "不统计" in (self.var_vd_category_mode.get() or "")
                    else "columns_plus_other"
                ),
                "vd_empty_to_other": self.var_vd_empty_to_other.get(),
                "vd_columns": self._read_vd_columns(),
            }
        if template == "roster":
            return {
                **shared,
                "roster_config_url": self.var_roster_config_url.get().strip(),
                "roster_config_sheet": self.var_roster_config_sheet.get().strip(),
                "roster_start_row": self.var_roster_start_row.get().strip(),
                "roster_target_url": self.var_roster_target_url.get().strip(),
                "roster_traffic_sheet": self.var_roster_traffic_sheet.get().strip(),
                "roster_date_start_row": self.var_roster_date_start.get().strip(),
                "roster_columns": self._read_roster_columns(),
            }
        return {
            **shared,
            "target_url": self.var_target_url.get().strip(),
            "hot_target_url": self.var_hot_target_url.get().strip(),
            "output_sheet": self.var_output_sheet.get().strip(),
            "hot_output_sheet": self.var_hot_output_sheet.get().strip(),
            "output_start_row": self.var_output_start_row.get().strip(),
            "hot_start_row": self.var_hot_start_row.get().strip(),
            "start_date": self.var_start.get().strip(),
            "end_date": self.var_end.get().strip(),
            "likes_threshold": self.var_likes.get().strip(),
            "schedule_minutes": self.var_minutes.get().strip(),
            "exclude_id_value": self.var_exclude.get().strip(),
            "date_field": self.var_date_field.get().strip(),
            "sort_field": self.var_sort_field.get().strip(),
            "cf_publish_url": self.var_cf_url.get().strip(),
            "cf_publish_secret": self.var_cf_secret.get().strip(),
            "cf_publish_source": self.var_cf_source.get().strip() or "all",
            "include_headers": self.var_include_headers.get(),
            "hot_include_headers": self.var_hot_include_headers.get(),
            "add_source_column": self.var_add_source_column.get(),
            "sort_descending": self.var_sort_desc.get(),
            "write_all": self.var_write_all.get(),
            "write_hot": self.var_write_hot.get(),
            "upsert_by_id": self.var_upsert.get(),
            "schedule_enabled": self.var_sched.get(),
            "schedule_only_if_changed": self.var_changed.get(),
            "cf_publish_after_sync": self.var_cf_after.get(),
            "sources": self._read_src("_src_rows"),
            "fields": self._read_fields(),
        }

    def _apply_settings_slice(self, settings: dict, template: str) -> None:
        s = settings or {}
        if template == "worksheet_merge":
            defaults = asdict(Config())
            from worksheet_channels import normalize_channels,SHARED_FIELDS,KINDS
            for key, var in self._wm_vars.items():
                if key in SHARED_FIELDS:self._set_str(var,s.get(key),str(defaults[key]))
            self._wm_channels=normalize_channels(s)
            for row in self._wm_channels:self._wm_enabled_vars[row['id']].set(row['enabled'])
            desired=s.get('wm_active_channel') or s.get('wm_media_type')
            if desired not in KINDS:desired=next((row['id'] for row in self._wm_channels if row['enabled']),'image')
            self._set_bool(self.var_wm_schedule_enabled,s.get('wm_schedule_enabled'))
            self._set_bool(self.var_wm_schedule_merge,s.get('wm_schedule_merge'),True)
            self._set_bool(self.var_wm_schedule_publish,s.get('wm_schedule_publish'),True)
            self._wm_load_channel(desired)
            self.var_wm_auto_publish.set(bool(s.get('wm_publish_after_sync')))
            self._refresh_wm_cloudflare()
            return
        if template == "catalog":
            self._set_str(self.var_catalog_index_url, s.get("catalog_index_url"))
            self._set_str(self.var_catalog_index_sheet, s.get("catalog_index_sheet"))
            self._set_str(self.var_catalog_start_row, s.get("catalog_start_row"), "2")
            self._set_str(self.var_catalog_url_col, s.get("catalog_url_col"), "B")
            self._set_str(self.var_catalog_sheet_col, s.get("catalog_sheet_col"), "D")
            self._set_str(self.var_catalog_target_url, s.get("catalog_target_url"))
            self._set_str(self.var_catalog_output_sheet, s.get("catalog_output_sheet"), "目录汇总")
            self._set_str(self.var_catalog_output_start_row, s.get("catalog_output_start_row"), "1")
            self._set_str(self.var_catalog_dedupe_col, s.get("catalog_dedupe_col"))
            self._set_bool(self.var_catalog_keep_header, s.get("catalog_keep_each_header"))
            self._set_bool(self.var_catalog_add_source, s.get("catalog_add_source"), True)
            self._set_bool(self.var_catalog_skip_existing, s.get("catalog_skip_existing"), True)
            self._set_bool(self.var_catalog_date_filter, s.get("catalog_date_filter_enabled"), True)
            self._set_str(self.var_catalog_date_col, s.get("catalog_date_col"))
            self._set_str(self.var_catalog_start, s.get("catalog_start_date"))
            self._set_str(self.var_catalog_end, s.get("catalog_end_date"))
            self._set_bool(self.var_catalog_sched, s.get("catalog_schedule_enabled"))
            self._set_str(self.var_catalog_minutes, s.get("catalog_schedule_minutes"), "180")
            self._set_catalog_exclude(s.get("catalog_exclude_sheets") or [])
            return
        if template == "posts":
            self._apply_posts_settings(s)
            return
        if template == "align":
            self._set_str(self.var_align_source_sheet, s.get("align_source_sheet"))
            self._set_str(self.var_align_header_row, s.get("align_header_row"), "1")
            self._set_str(self.var_align_target_url, s.get("align_target_url"))
            self._set_str(self.var_align_output_sheet, s.get("align_output_sheet"), "对齐结果")
            self._set_str(self.var_align_start_row, s.get("align_start_row"), "1")
            self._set_bool(self.var_align_include_headers, s.get("align_include_headers"), True)
            self._set_str(self.var_align_minutes, s.get("align_schedule_minutes"), "60")
            self._set_bool(self.var_align_sched, s.get("align_schedule_enabled"))
            self._set_bool(self.var_align_changed, s.get("align_schedule_only_if_changed"), True)
            self._set_src(self.align_box, "_align_rows", "例如：8月份", self.align_count, s.get("align_sources"), align=True)
            mappings = s.get("align_mappings") or [{"target": header, "source": header} for header in (s.get("align_headers") or [])]
            self._align_default_mappings = copy.deepcopy(mappings)
            self._align_profiles = copy.deepcopy(s.get("align_mapping_profiles") or {})
            self._align_profile_key = "__default__"
            self._write_align_mappings(self._align_default_mappings)
            self._refresh_align_profiles()
            return
        if template == "sheet_metrics":
            self._set_str(self.var_sm_source_url, s.get("sm_source_url"))
            self.var_sm_source_mode.set("只扫描指定工作表" if s.get("sm_source_mode") == "single" else "扫描全部工作表")
            self._set_str(self.var_sm_source_sheet, s.get("sm_source_sheet"))
            self._set_str(self.var_sm_exclude_exact, "，".join(s.get("sm_exclude_exact") or []))
            self._set_str(self.var_sm_exclude_contains, "，".join(s.get("sm_exclude_contains") or []))
            self._set_str(self.var_sm_source_code_row, s.get("sm_source_code_row"), "1")
            self._set_str(self.var_sm_source_name_row, s.get("sm_source_name_row"), "2")
            self._set_str(self.var_sm_source_date_col, s.get("sm_source_date_col"), "A")
            self._set_str(self.var_sm_source_data_start_row, s.get("sm_source_data_start_row"), "5")
            self._set_str(self.var_sm_source_metric_start_col, s.get("sm_source_metric_start_col"), "F")
            self._set_str(self.var_sm_source_group_width, s.get("sm_source_group_width"), "6")
            self._set_str(self.var_sm_source_metric_count, s.get("sm_source_metric_count"), "4")
            self._set_str(self.var_sm_target_url, s.get("sm_target_url"))
            self._set_str(self.var_sm_target_sheet, s.get("sm_target_sheet"))
            self._set_str(self.var_sm_target_name_col, s.get("sm_target_name_col"), "A")
            self._set_str(self.var_sm_target_code_col, s.get("sm_target_code_col"), "B")
            self._set_str(self.var_sm_target_date_row, s.get("sm_target_date_row"), "3")
            self._set_str(self.var_sm_target_data_start_row, s.get("sm_target_data_start_row"), "7")
            self._set_str(self.var_sm_target_group_start_col, s.get("sm_target_group_start_col"), "L")
            self._set_str(self.var_sm_target_group_width, s.get("sm_target_group_width"), "8")
            self._set_str(self.var_sm_target_write_count, s.get("sm_target_write_count"), "4")
            self.var_sm_date_mode.set("按完整年月日匹配" if s.get("sm_date_mode") == "full_date" else "按月日匹配")
            self._set_str(self.var_sm_batch_size, s.get("sm_batch_size"), "100")
            return
        if template in ("video", "custom"):
            self._set_str(self.var_vd_source_url, s.get("vd_source_url"))
            source_sheets = s.get("vd_source_sheets") or []
            self._set_str(
                self.var_vd_source_sheet,
                ", ".join(source_sheets) if source_sheets else (s.get("vd_source_sheet") or ""),
            )
            self._set_str(self.var_vd_start_row, s.get("vd_start_row"), "2")
            self._set_str(self.var_vd_col_date, s.get("vd_col_date"), "A")
            self._set_str(self.var_vd_col_link, s.get("vd_col_link"), "B")
            self._set_str(self.var_vd_col_name, s.get("vd_col_name"), "H")
            self._set_str(self.var_vd_col_type, s.get("vd_col_type"), "E")
            self._set_vd_types(s.get("vd_types") or [])
            self._set_vd_report_cats(s.get("vd_report_categories") or [])
            self._set_vd_duration_rules(s.get("vd_duration_rules") or self._default_vd_duration_rules())
            self._set_str(self.var_vd_dest_url, s.get("vd_dest_url"))
            self._set_str(self.var_vd_log_sheet, s.get("vd_log_sheet"), "日志表")
            self._set_str(self.var_vd_report_sheet, s.get("vd_report_sheet"), "数据表")
            self._set_str(self.var_vd_out_start_row, s.get("vd_out_start_row"), "1")
            self._set_str(self.var_vd_unit, s.get("vd_unit_seconds"), "30")
            self.var_vd_count_mode.set(
                {"per_video_ceil": "逐条视频按30秒计数", "duration_buckets": "按时长区间统计数量"}.get(
                    s.get("vd_count_mode"), "汇总总秒数 ÷ 30"
                )
            )
            self._set_bool(self.var_vd_include_headers, s.get("vd_include_headers"), True)
            self._set_str(self.var_vd_start, s.get("vd_start_date"))
            self._set_str(self.var_vd_end, s.get("vd_end_date"))
            self._set_bool(self.var_vd_schedule_enabled, s.get("vd_schedule_enabled"))
            self._set_str(self.var_vd_schedule_minutes, s.get("vd_schedule_minutes"), "180")
            self._set_bool(self.var_vd_date_filter_enabled, s.get("vd_date_filter_enabled"), True)
            self.var_vd_type_filter_mode.set(
                {"include": "只包含这些类型", "exclude": "排除这些类型", "all": "不筛选类型"}.get(s.get("vd_type_filter_mode") or "include", "只包含这些类型")
            )
            self._set_str(self.var_vd_other_category, s.get("vd_other_category"))
            exclude = list(s.get("vd_exclude_types") or [])
            type_names = {
                str(item.get("name") if isinstance(item, dict) else item).strip()
                for item in (s.get("vd_types") or [])
                if str(item.get("name") if isinstance(item, dict) else item).strip()
            }
            if type_names and exclude and type_names == {str(x).strip() for x in exclude}:
                exclude = []
            self._set_vd_exclude_types(exclude)
            self.var_vd_category_mode.set(
                "这些分类不统计，未分类和其他全部归入其余"
                if s.get("vd_category_mode") == "other_only"
                else "分类单独成列，未分类和其他归入其余"
            )
            self._set_bool(self.var_vd_empty_to_other, s.get("vd_empty_to_other"), True)
            if template == "custom" and hasattr(self, "vd_category_mode_combo"):
                self._apply_vd_category_mode()
            self._set_vd_columns(s.get("vd_columns") or [])
            return
        if template == "roster":
            self._set_str(self.var_roster_config_url, s.get("roster_config_url"))
            self._set_str(self.var_roster_config_sheet, s.get("roster_config_sheet"))
            self._set_str(self.var_roster_start_row, s.get("roster_start_row"), "2")
            self._set_str(self.var_roster_target_url, s.get("roster_target_url"))
            self._set_str(self.var_roster_traffic_sheet, s.get("roster_traffic_sheet"), "流量")
            self._set_str(self.var_roster_date_start, s.get("roster_date_start_row"), "24")
            self._set_roster_columns(s.get("roster_columns") or [])
            return
        self._set_str(self.var_start, s.get("start_date"))
        self._set_str(self.var_end, s.get("end_date"))
        self._set_str(self.var_likes, s.get("likes_threshold"), "1000")
        self._set_bool(self.var_add_source_column, s.get("add_source_column"), True)
        self._set_bool(self.var_upsert, s.get("upsert_by_id"), True)
        self._set_bool(self.var_write_all, s.get("write_all"), True)
        self._set_bool(self.var_write_hot, s.get("write_hot"))
        self._filter_cf_profile_id = str(s.get('cf_profile_id') or '')
        self._set_str(self.var_target_url, s.get("target_url"))
        self._set_str(self.var_output_sheet, s.get("output_sheet"), "筛选结果")
        self._set_str(self.var_output_start_row, s.get("output_start_row"), "1")
        self._set_bool(self.var_include_headers, s.get("include_headers"))
        self._set_str(self.var_hot_target_url, s.get("hot_target_url"))
        self._set_str(self.var_hot_output_sheet, s.get("hot_output_sheet"), "高亮结果")
        self._set_str(self.var_hot_start_row, s.get("hot_start_row"), "1")
        self._set_bool(self.var_hot_include_headers, s.get("hot_include_headers"), True)
        self._set_str(self.var_cf_url, s.get("cf_publish_url"))
        self._set_str(self.var_cf_secret, s.get("cf_publish_secret"))
        self._set_str(self.var_cf_source, s.get("cf_publish_source"), "all")
        self._set_bool(self.var_cf_after, s.get("cf_publish_after_sync"), True)
        self._set_str(self.var_minutes, s.get("schedule_minutes"), "1440")
        self._set_bool(self.var_sched, s.get("schedule_enabled"))
        self._set_bool(self.var_changed, s.get("schedule_only_if_changed"), True)
        self._set_str(self.var_exclude, s.get("exclude_id_value"), "未找到")
        self._set_str(self.var_date_field, s.get("date_field"), "发布日期")
        self._set_str(self.var_sort_field, s.get("sort_field"), "点赞")
        self._set_bool(self.var_sort_desc, s.get("sort_descending"), True)
        self._set_src(self.src_box, "_src_rows", "例如：分组A", self.src_count, s.get("sources") or s.get("source_urls"))
        wanted = s.get("fields") or copy_default_fields()
        if len(self._field_rows) != len(wanted):
            for row in list(self._field_rows):
                row.destroy()
            self._field_rows = []
            for field in wanted:
                self._add_field(field)
        else:
            for row, field in zip(self._field_rows, wanted):
                if isinstance(field, dict):
                    self._fill_entry(row._name, str(field.get("name") or ""))
                    self._fill_entry(row._sheet, str(field.get("sheet") or ""))
                    self._fill_entry(row._range, str(field.get("range") or ""))

    def _payload(self) -> dict:
        return asdict(self.cfg)

    def _save(self, quiet: bool = False) -> None:
        self._store_current_menu_settings()
        cfg = self._cfg_for_action()
        current = next((item for item in self._menus if item["id"] == self._active_menu_id), None)
        if current and current.get('template')=='worksheet_merge' and cfg.wm_schedule_enabled:
            from app import _validate_worksheet_sync
            try:_validate_worksheet_sync(cfg)
            except (ValueError,TypeError) as exc:
                if not quiet:messagebox.showwarning('定时设置',str(exc),parent=self)
                return
        save_config(cfg)
        self.cfg = cfg
        current_template = current.get("template") if current else ""
        if current_template in ("video", "custom") and self.var_vd_schedule_enabled.get():
            if not self.var_vd_source_url.get().strip() or not self.var_vd_dest_url.get().strip():
                self.var_vd_schedule_enabled.set(False)
                current["settings"]["vd_schedule_enabled"] = False
                cfg.vd_schedule_enabled = False
                save_config(cfg)
                if not quiet:
                    messagebox.showwarning("数据汇总工具", "视频/分类定时需要先填写源表和目标表链接")
        if current_template == "posts" and self.var_pa_sched.get():
            if not self.var_pa_list_url.get().strip():
                self.var_pa_sched.set(False)
                current["settings"]["pa_schedule_enabled"] = False
                cfg.pa_schedule_enabled = False
                save_config(cfg)
                if not quiet:
                    messagebox.showwarning("数据汇总工具", "多表汇总定时需要先填写数据列表表格链接")
        self._sync_schedulers()
        if not quiet:
            self._append_log("已保存配置，各菜单的定时会按各自间隔执行")

    def _sync_schedulers(self) -> None:
        self._store_current_menu_settings()
        sync_schedulers_from_menus(self._menus)

    def _cfg_for_action(self):
        """Build a complete config while retaining the editable menu definitions."""
        self._store_current_menu_settings()
        current = next((item for item in self._menus if item["id"] == self._active_menu_id), None)
        settings = (current or {}).get("settings") or {}
        cfg = _cfg_from_payload(settings, base=Config())
        cfg.ui_menus = copy.deepcopy(self._menus)
        cfg.ui_archived_menus = copy.deepcopy(self.cfg.ui_archived_menus)
        cfg.ui_active_menu = self._active_menu_id
        self._apply_creds_to_cfg(cfg)
        return cfg

    def _run_now(self) -> None:
        cfg = self._cfg_for_action()
        err = start_filter_job(cfg, from_schedule=False)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("筛选汇总已加入队列")
        self._set_badge("排队中", C["accent"])

    def _run_align(self) -> None:
        cfg = self._cfg_for_action()
        err = start_align_job(cfg)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("表头对齐已加入队列")
        self._set_badge("排队中", C["accent"])

    def _run_catalog(self) -> None:
        cfg = self._cfg_for_action()
        err = start_catalog_job(cfg)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("目录汇总已加入队列")
        self._set_badge("排队中", C["accent"])

    def _run_posts(self) -> None:
        cfg = self._cfg_for_action()
        err = start_posts_job(cfg)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("多表汇总已加入队列")
        self._set_badge("排队中", C["accent"])

    def _run_worksheet_merge(self) -> None:
        err = start_worksheet_merge_job(self._cfg_for_action())
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("多工作表汇总已加入队列")
        self._set_badge("排队中", C["accent"])

    def _publish_worksheet_gallery(self) -> None:
        err = start_worksheet_publish_job(self._cfg_for_action())
        if err:
            messagebox.showwarning('图库发布',err)
            return
        self._log_n = 0
        self._append_log('汇总图库发布已加入队列（软件直推 Cloudflare）')
        self._set_badge('排队中',C['accent'])

    def _repair_worksheet_links(self) -> None:
        err=start_worksheet_links_job(self._cfg_for_action())
        if err:
            messagebox.showwarning('修复超链接',err)
            return
        self._log_n=0
        self._append_log('超链接修复已加入队列：只补回与来源完全匹配的原链接，不重写数据或发布图库')
        self._set_badge('排队中',C['accent'])

    def _run_roster(self) -> None:
        cfg = self._cfg_for_action()
        err = start_roster_job(cfg)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("队别专页汇总已加入队列")
        self._set_badge("排队中", C["accent"])

    def _run_sheet_metrics(self, preview: bool = False) -> None:
        cfg = self._cfg_for_action()
        err = start_sheet_metrics_job(cfg, preview=preview)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("指标同步预览已加入队列" if preview else "多工作表指标同步已加入队列")
        self._set_badge("预览排队中" if preview else "排队中", C["accent"])

    def _publish(self) -> None:
        cfg = self._cfg_for_action()
        err = start_publish_job(cfg)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._set_badge("发布中", C["accent"])

    def _run_video(self) -> None:
        cfg = self._cfg_for_action()
        err = start_video_job(cfg)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("视频任务已加入队列")
        self._set_badge("排队中", C["accent"])

    def _force_refresh_vd_header(self) -> None:
        """Run one video aggregation that deliberately rebuilds report headers and styles."""
        cfg = self._cfg_for_action()
        cfg.vd_force_refresh_header = True
        err = start_video_job(cfg)
        if err:
            messagebox.showwarning("数据汇总工具", err)
            return
        self._log_n = 0
        self._append_log("已加入强制刷新任务：将重建数据表表头、数据和样式")
        self._set_badge("排队中", C["accent"])

    def _stop_current(self) -> None:
        stop_job(self._active_menu_id or "default")
        self._append_log("已请求停止当前配置")

    def _stop_all(self) -> None:
        stop_all_jobs()
        self._append_log("已请求停止全部任务")

    def _run_selected(self) -> None:
        starters = {
            "filter": start_filter_job,
            "posts": start_posts_job,
            "video": start_video_job,
            "worksheet_merge": start_worksheet_merge_job,
        }
        queued = 0
        self._store_current_menu_settings()
        for item in self._menus:
            if not self._menu_checks.get(item["id"], tk.BooleanVar(value=False)).get():
                continue
            settings = item.get("settings") or {}
            cfg = _cfg_from_payload(settings, base=Config())
            cfg.ui_active_menu = item["id"]
            cfg.ui_menus = self._menus
            cfg.ui_archived_menus = copy.deepcopy(self.cfg.ui_archived_menus)
            self._apply_creds_to_cfg(cfg)
            fn = starters.get(item["template"])
            if not fn:
                continue
            err = fn(cfg)
            if err:
                self._append_log(f"{item['name']} 未排队：{err}")
            else:
                queued += 1
                self._append_log(f"{item['name']} 已加入队列")
        if queued:
            self._log_n = 0
            self._set_badge("排队中", C["accent"])
        else:
            messagebox.showinfo("数据汇总工具", "没有可执行的配置。请在左侧勾选要跑的菜单。")

    def _peek_headers(self) -> None:
        cfg = self._cfg_for_action()
        srcs = cfg.align_sources or []
        source = next((item for item in srcs if item.get("url") == self._align_profile_key), srcs[0] if srcs else None)
        if not source or not source.get("url"):
            messagebox.showwarning("数据汇总工具", "请先填写至少一个数据源链接")
            return

        def work():
            try:
                headers = peek_source_headers(
                    cfg,
                    source["url"],
                    sheet_name=source.get("sheet") or cfg.align_source_sheet or "",
                    header_row=int(cfg.align_header_row or 1),
                )
                self.after(0, lambda: self._fill_headers(headers))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("数据汇总工具", str(e)))

        threading.Thread(target=work, daemon=True).start()
        self._append_log("正在读取源表表头…")

    def _fill_headers(self, headers):
        mappings = [{"target": header, "source": header} for header in (headers or [])]
        self._write_align_mappings(mappings)
        self._save_align_profile()
        self._append_log(f"读到 {len(headers or [])} 个表头")

    def _job_done_status(self, job: dict) -> str:
        result = job.get("result") or {}
        finished = job.get("finished_at") or ""
        prefix = f"已完成 {finished}  " if finished else "已完成  "
        mode = result.get("mode")
        if mode=='worksheet_sync':
            return prefix+f"汇总 {result.get('merged_rows',0)} 行 · 发布 {result.get('published_rows',0)} 条"
        if mode == "posts":
            added = int(result.get("total_rows") or 0)
            ok_sheets = int(result.get("ok_sheets") or 0)
            failed = int(result.get("failed_sheets") or 0)
            date_skip = int(result.get("date_skipped") or 0)
            library_added = int(result.get("library_added") or 0)
            library_skipped = int(result.get("library_skipped") or 0)
            parts = [f"整合表 {added} 行", f"对照表新增 {library_added} 行", f"成功 {ok_sheets} 个明细表"]
            if library_skipped:
                parts.append(f"对照表已有跳过 {library_skipped}")
            if failed:
                parts.append(f"失败 {failed}")
            if date_skip:
                parts.append(f"日期外 {date_skip}")
            return prefix + "，".join(parts)
        if mode == "catalog":
            added = int(result.get("total_rows") or 0)
            existing = int(result.get("existing_rows") or 0)
            total = int(result.get("sheet_total") or (existing + added))
            parts = [f"本轮新增 {added} 行", f"目标表一共 {total} 行"]
            skipped = int(result.get("skipped") or 0)
            date_skip = int(result.get("date_skipped") or 0)
            ok_sheets = int(result.get("ok_sheets") or 0)
            if skipped:
                parts.append(f"重复跳过 {skipped}")
            if date_skip:
                parts.append(f"日期外 {date_skip}")
            if ok_sheets:
                parts.append(f"成功 {ok_sheets} 个工作表")
            return prefix + "，".join(parts)
        if mode in ("video", "video_custom"):
            return prefix + f"{result.get('people') or 0} 人 · 本次 {result.get('appended') or 0} 条"
        if mode == 'worksheet_link_repair':
            return prefix + f"已修复 {result.get('total_rows') or 0} 个超链接；不匹配 {result.get('unmatched') or 0} 行，冲突 {result.get('ambiguous') or 0} 个"
        if result.get('channels') and mode in ('worksheet_media_merge','worksheet_publish'):
            parts=[]
            for kind,label in (('image','图片'),('video','KB视频'),('effect_video','效果视频')):
                row=result['channels'].get(kind)
                if row is not None:parts.append(f"{label} {row.get('total_rows',row.get('totalRows',0))} 条")
            return prefix+' · '.join(parts)
        if mode in ("sheet_metric_preview", "sheet_metric_sync"):
            if mode == "sheet_metric_preview":
                return prefix + (
                    f"预览：匹配 {result.get('matched') or 0} 组，预计修改 "
                    f"{result.get('planned_cells') or 0} 格，未写入数据"
                )
            return prefix + (
                f"指标同步：匹配 {result.get('matched') or 0} 组，确认写入 "
                f"{result.get('written_cells') or 0} 格"
            )
        if result.get("skipped") and not result.get("total_rows"):
            return prefix + "内容未变化，已跳过"
        if "total_rows" in result:
            return prefix + f"写入 {result.get('total_rows') or 0} 行"
        return prefix.strip()

    def _set_badge(self, text, bg):
        if not self._alive:
            return
        if getattr(self, "_badge_value", None) == (text, bg):
            return
        try:
            self.badge.configure(text=text, bg=bg)
            self._badge_value = (text, bg)
        except Exception:
            pass

    def _append_log(self, msg: str) -> None:
        if not self._alive:
            return
        try:
            self.log.configure(state="normal")
            self.log.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except Exception:
            return
        try:
            write_app_log(msg)
        except Exception:
            pass

    def _tick(self) -> None:
        if not self._alive:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            job = job_snapshot(self._active_menu_id or "default")
            logs = job.get("logs") or []
            if self._log_n > len(logs):
                self._log_n = 0
            if len(logs) > self._log_n:
                chunk = logs[self._log_n :]
                if len(chunk) > 150:
                    chunk = chunk[-150:]
                self.log.configure(state="normal")
                self.log.insert("end", "".join(f"[{line.get('t','')}] {line.get('msg','')}\n" for line in chunk))
                self._log_n = len(logs)
                try:
                    if float(self.log.index("end-1c").split(".")[0]) > 900:
                        self.log.delete("1.0", "end-600l")
                except Exception:
                    pass
                self.log.see("end")
                self.log.configure(state="disabled")
            running = bool(job.get("running"))
            queued = bool(job.get("queued"))
            mine = menu_schedule_snapshot(self._active_menu_id or "")
            snap = _schedule_snapshot()
            asnap = _align_schedule_snapshot()
            vsnap = _video_schedule_snapshot()
            csnap = _catalog_schedule_snapshot()
            psnap = _posts_schedule_snapshot()
            tab = getattr(self, "_tab", "")
            current_snap = mine if mine.get("enabled") else {
                "filter": snap,
                "align": asnap,
                "video": vsnap,
                "custom": vsnap,
                "catalog": csnap,
                "posts": psnap,
            }.get(tab, {"enabled": False})
            nxt = current_snap.get("next_run") or ""
            if running:
                self.status.set("运行中…  " + (logs[-1]["msg"] if logs else ""))
                self._set_badge("运行中", C["accent"])
            elif queued:
                self.status.set("已在队列中等待…")
                self._set_badge("排队中", C["accent"])
            elif job.get("error"):
                self.status.set("失败：" + str(job.get("error")))
                self._set_badge("失败", C["bad"])
            elif job.get("result"):
                self.status.set(self._job_done_status(job))
                self._set_badge("已完成", C["ok"])
            else:
                self._set_badge("待命", C["sa"])
                if current_snap.get("enabled"):
                    extra = f"  下次 {nxt}" if nxt else ""
                    last = current_snap.get("last_sync") or ""
                    if last:
                        extra += f"  上次 {last.replace('T', ' ')}"
                    self.status.set(f"定时已开 · 每 {current_snap.get('minutes')} 分钟" + extra)
                else:
                    self.status.set("待命")
            self._set_sched_label("sched_info", snap)
            self._set_sched_label("align_sched_info", asnap)
            self._set_sched_label("vd_sched_info", mine if tab in ("video", "custom") else vsnap)
            self._set_sched_label("catalog_sched_info", mine if tab == "catalog" else csnap)
            self._set_sched_label("posts_sched_info", mine if tab == "posts" else psnap)
            self._set_sched_label('wm_sched_info',mine if tab=='worksheet_merge' else {'enabled':False})
        except Exception as exc:
            try:
                write_app_log(f"界面刷新失败: {exc}")
            except Exception:
                pass
        if self._alive:
            try:
                self._tick_id = self.after(1500, self._tick)
            except Exception:
                pass

    def _set_sched_label(self, attr: str, snap: dict) -> None:
        widget = getattr(self, attr, None)
        if widget is None:
            return
        if snap.get("enabled"):
            text = f"已启动 · 每 {snap.get('minutes')} 分钟 · 下次 {snap.get('next_run') or '-'}"
            if attr=='wm_sched_info' and snap.get('last_msg'):text+=' · '+snap['last_msg']
        else:
            text = "定时未启动"
        if self._tick_cache.get(attr) == text:
            return
        self._tick_cache[attr] = text
        widget.configure(text=text)

    def _on_close(self) -> None:
        try:
            self._persist_credentials()
        except Exception:
            pass
        self._alive = False
        try:
            write_app_log("窗口关闭")
        except Exception:
            pass
        if self._tick_id is not None:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
        try:
            stop_all_menu_schedulers()
            stop_scheduler()
            stop_align_scheduler()
            stop_video_scheduler()
            stop_catalog_scheduler()
        except Exception:
            pass
        self.destroy()


def _serve_window_requests(app, mutex) -> None:
    """Local, read-only restore-window IPC for Finder / repeated launches."""
    mutex.settimeout(0.5)
    def serve():
        while app._alive:
            try:
                client, _address = mutex.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                try:
                    client.settimeout(0.5)
                    if client.recv(64) != b'sheets-summary:show-window:v1':
                        continue
                    app.after(0, app.show_main_window)
                    client.sendall(b'sheets-summary:window-restored:v1')
                except (OSError, RuntimeError, tk.TclError):
                    pass
    threading.Thread(target=serve,daemon=True).start()


def main(*, on_ready=None) -> None:
    os.chdir(SCRIPT_DIR)
    try:
        import faulthandler

        crash_path = LOG_DIR / "crash.log"
        crash_file = open(crash_path, "a", encoding="utf-8", errors="replace")
        crash_file.write(f"\n---- {datetime.now().isoformat(timespec='seconds')} ----\n")
        crash_file.flush()
        faulthandler.enable(file=crash_file, all_threads=True)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        write_app_log("程序启动")
    except Exception:
        pass
    mutex = _mutex_socket()
    if mutex is None:
        if _bring_existing_window():
            return
        if _kill_stale_mutex():
            time.sleep(0.5)
            mutex = _mutex_socket()
        if mutex is None:
            if _bring_existing_window():
                return
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("数据汇总工具", "已经在运行，请看任务栏里的「数据汇总工具」。")
            root.destroy()
            return
    app = DesktopApp()
    app._mutex = mutex
    _serve_window_requests(app, mutex)
    app.after_idle(app.show_main_window)
    if on_ready is not None:
        on_ready(app)
    try:
        app.mainloop()
    except Exception:
        err = traceback.format_exc()
        try:
            write_app_log("主循环异常", err)
            (SCRIPT_DIR / "desktop_error.log").write_text(err, encoding="utf-8")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        err = traceback.format_exc()
        try:
            (SCRIPT_DIR / "desktop_error.log").write_text(err, encoding="utf-8")
        except Exception:
            pass
        raise
