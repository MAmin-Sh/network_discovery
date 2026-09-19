import atexit
import csv
import concurrent.futures
import ipaddress
import json
import os
import platform
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

try:
    import customtkinter as ctk
except ImportError:
    raise SystemExit(
        "CustomTkinter is required.\n"
        "Install it with:\n\npip install customtkinter"
    )


# =====================================================================
# Application identity
# =====================================================================
APP_NAME    = "Network Operations Console"
APP_VERSION = "2.6.1"
APP_ID      = "AminShirinkar.NetworkOpsConsole.2.6"
APP_PNG     = "app.png"


# =====================================================================
# High-DPI awareness
# =====================================================================
def enable_high_dpi():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# =====================================================================
# PyInstaller helpers
# =====================================================================
def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS            # type: ignore[attr-defined]
    except AttributeError:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


def set_windows_taskbar_identity(app_id: str = APP_ID) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


# =====================================================================
# Icon pipeline
# =====================================================================
def _load_app_image():
    path = resource_path(APP_PNG)
    if not os.path.isfile(path):
        return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def _build_windows_ico(pil_img):
    try:
        fd, ico_path = tempfile.mkstemp(prefix="app_icon_", suffix=".ico")
        os.close(fd)
        sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
                 (64, 64), (128, 128), (256, 256)]
        pil_img.save(ico_path, format="ICO", sizes=sizes)
        atexit.register(lambda p=ico_path: _safe_remove(p))
        return ico_path
    except Exception:
        return None


def _safe_remove(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def apply_app_icon(window: tk.Misc, pil_img) -> None:
    if pil_img is None:
        return
    try:
        big = pil_img.resize((256, 256), Image.LANCZOS)
        photo = ImageTk.PhotoImage(big)
        window.iconphoto(True, photo)
        window._icon_photo_ref = photo
    except Exception:
        pass

    if sys.platform == "win32":
        ico_path = _build_windows_ico(pil_img)
        if ico_path:
            try:
                window.iconbitmap(ico_path)
                window._icon_ico_path = ico_path
            except tk.TclError:
                pass


# =====================================================================
# Palette
# =====================================================================
COLORS = {
    "bg":           "#080C17",
    "surface":      "#0F1729",
    "surface_2":    "#16213A",
    "surface_3":    "#1F2C47",
    "surface_4":    "#28375A",
    "border":       "#26334D",
    "border_soft":  "#1A2438",
    "border_strong":"#324566",

    "text":         "#E6ECF5",
    "text_dim":     "#9BA9BF",
    "text_muted":   "#5F7186",

    "blue":         "#3B82F6",
    "blue_dark":    "#2563EB",
    "blue_soft":    "#1E3A8A",
    "green":        "#22C55E",
    "green_dark":   "#16A34A",
    "yellow":       "#F59E0B",
    "red":          "#EF4444",

    "row_alt":      "#121B30",
    "shadow":       "#03060E",

    "navy_950":     "#080C17",
    "navy_900":     "#0F1729",
    "navy_800":     "#16213A",
    "slate_700":    "#26334D",
    "slate_600":    "#475569",
    "slate_500":    "#64748B",
    "slate_400":    "#94A3B8",
    "slate_300":    "#CBD5E1",
    "slate_200":    "#E2E8F0",
    "white":        "#FFFFFF",
}

MONO  = ("Consolas", 10)
UI    = "Segoe UI"
UI_S  = "Segoe UI Semibold"

# Toast geometry
TOAST_W        = 440
TOAST_H        = 56
TOAST_TOP      = 112
TOAST_GAP      = 10
TOAST_DURATION = 3200
TOAST_ANIM_MS  = 220
TOAST_FRAME_MS = 16

# ---- table heading height (px) ---------------------------------------
# Matches "Enterprise.Treeview.Heading" style below:
#   font = Segoe UI Semibold 9 (~14 px line-height)
#   padding = (10, 16)        (~32 px vertical)
#   => total ≈ 46-48 px. 48 keeps the overlay safely below the header.
HEADER_PX = 48


# =====================================================================
class NetworkScannerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(f"{APP_NAME}  |  v{APP_VERSION}")
        self.geometry("1240x880")
        self.minsize(1100, 760)
        self.configure(fg_color=COLORS["bg"])

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._app_pil_img = _load_app_image()
        self.after(150, lambda: apply_app_icon(self, self._app_pil_img))

        # runtime state
        self.stop_event      = threading.Event()
        self.scan_thread     = None
        self.scan_started_at = None
        self.results         = []
        self.filtered_results = []
        self.event_queue     = queue.Queue()

        self.total_hosts   = 254
        self.scanned_count = 0
        self.online_count  = 0

        # own-IP state
        self.local_ip_value     = None
        self.public_ip_value    = None
        self._last_auto_ip      = None
        self._ip_refresh_active = False
        self._refresh_dot_count = 0

        # toasts
        self._toasts = []

        # ----- table state machine ----------------------------------
        # "_state_mode" ∈ {"hidden", "loading", "empty"} — only one at a time.
        self._state_mode        = "hidden"
        self._loading_frame_idx = 0
        self._loading_after_id  = None
        self._table_host        = None   # set in _build_results_card
        self._state_overlay     = None   # set in _build_results_card

        self._configure_ttk()
        self._build_ui()
        self._bind_shortcuts()
        self._detect_network()

        # initial visual: empty state
        self.after(60, self._show_empty_state)

        self.after(100, self._process_events)
        self.after(1000, self._refresh_clock)

    # ==================================================================
    # ttk styling
    # ==================================================================
    def _configure_ttk(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(
            "Enterprise.Treeview",
            background=COLORS["surface_2"],
            fieldbackground=COLORS["surface_2"],
            foreground=COLORS["text"],
            rowheight=44,
            font=(UI, 10),
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Enterprise.Treeview.Heading",
            background=COLORS["surface_3"],
            foreground=COLORS["text_dim"],
            font=(UI_S, 9),
            relief="flat",
            padding=(10, 16),
            borderwidth=0,
        )
        style.map(
            "Enterprise.Treeview",
            background=[("selected", COLORS["blue_soft"])],
            foreground=[("selected", COLORS["white"])],
        )
        style.map(
            "Enterprise.Treeview.Heading",
            background=[("active", COLORS["surface_4"])],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=COLORS["surface_3"],
            troughcolor=COLORS["surface"],
            bordercolor=COLORS["surface"],
            arrowcolor=COLORS["text_dim"],
            borderwidth=0,
            arrowsize=14,
        )
        style.layout(
            "Enterprise.Treeview",
            [("Enterprise.Treeview.treearea", {"sticky": "nswe"})],
        )

    # ==================================================================
    # UI
    # ==================================================================
    def _build_ui(self):
        self._build_header()

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=26, pady=(18, 22))

        self._build_configuration_card(content)
        self._build_kpi_row(content)
        self._build_progress_card(content)
        self._build_results_card(content)

    def _build_header(self):
        header = ctk.CTkFrame(self, height=94, corner_radius=0,
                              fg_color=COLORS["navy_900"])
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkFrame(self, height=1, fg_color=COLORS["border"]).pack(fill="x")

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", padx=26, pady=16)

        if self._app_pil_img is not None:
            self._header_logo_image = ctk.CTkImage(
                light_image=self._app_pil_img,
                dark_image=self._app_pil_img,
                size=(44, 44),
            )
            logo = ctk.CTkLabel(
                left, image=self._header_logo_image, text="",
                width=48, height=48, fg_color="transparent",
            )
        else:
            logo = ctk.CTkLabel(
                left, text="N", width=48, height=48, corner_radius=11,
                fg_color=COLORS["blue"], text_color=COLORS["white"],
                font=(UI_S, 22),
            )
        logo.pack(side="left", padx=(0, 16))

        text_box = ctk.CTkFrame(left, fg_color="transparent")
        text_box.pack(side="left")

        ctk.CTkLabel(text_box, text=APP_NAME, text_color=COLORS["white"],
                     font=(UI_S, 20)).pack(anchor="w")

        ctk.CTkLabel(
            text_box,
            text="Enterprise network discovery, diagnostics & reporting",
            text_color=COLORS["text_muted"], font=(UI, 10),
        ).pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right", padx=26)

        self.header_status = ctk.CTkLabel(
            right, text="●  READY", text_color=COLORS["green"],
            font=(UI_S, 10),
        )
        self.header_status.pack(anchor="e", pady=(26, 0))

        ctk.CTkLabel(right, text=f"Version {APP_VERSION}",
                     text_color=COLORS["text_muted"],
                     font=(UI, 9)).pack(anchor="e")

    def _card(self, parent, shadow=True):
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        if shadow:
            ctk.CTkFrame(wrapper, fg_color=COLORS["shadow"], corner_radius=13,
                         ).place(relx=0, rely=0, relwidth=1, relheight=1,
                                 x=2, y=3)
            ctk.CTkFrame(wrapper, fg_color=COLORS["shadow"], corner_radius=12,
                         ).place(relx=0, rely=0, relwidth=1, relheight=1,
                                 x=1, y=2)

        card = ctk.CTkFrame(
            wrapper, fg_color=COLORS["surface"],
            border_width=1, border_color=COLORS["border_soft"],
            corner_radius=10,
        )
        card.place(relx=0, rely=0, relwidth=1, relheight=1)
        return wrapper, card

    def _section_title(self, parent, title, subtitle=None):
        ctk.CTkLabel(parent, text=title, text_color=COLORS["text"],
                     font=(UI_S, 13)).pack(anchor="w", padx=20, pady=(18, 0))
        if subtitle:
            ctk.CTkLabel(parent, text=subtitle, text_color=COLORS["text_muted"],
                         font=(UI, 9)).pack(anchor="w", padx=20, pady=(3, 13))

    def _build_configuration_card(self, parent):
        wrapper, card = self._card(parent)
        wrapper.pack(fill="x")
        wrapper.configure(height=252)

        self._section_title(
            card, "Network Configuration",
            "Scan the detected /24 subnet or specify another IPv4 address.",
        )

        info = ctk.CTkFrame(
            card, fg_color=COLORS["surface_2"], corner_radius=8,
            border_width=1, border_color=COLORS["border_soft"],
        )
        info.pack(fill="x", padx=20, pady=(0, 14))

        info_inner = ctk.CTkFrame(info, fg_color="transparent")
        info_inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(info_inner, text="LOCAL IP",
                     text_color=COLORS["text_muted"],
                     font=(UI_S, 8)).pack(side="left", padx=(0, 8))

        self.local_ip_label = ctk.CTkLabel(
            info_inner, text="…", text_color=COLORS["text_dim"],
            font=("Consolas", 11),
        )
        self.local_ip_label.pack(side="left", padx=(0, 2))

        self.local_ip_copy = ctk.CTkButton(
            info_inner, text="📋", command=self._copy_local_ip,
            width=28, height=28, corner_radius=6,
            fg_color="transparent", hover_color=COLORS["surface_3"],
            text_color=COLORS["text_dim"], font=(UI, 11),
        )
        self.local_ip_copy.pack(side="left")

        ctk.CTkFrame(info_inner, width=1, height=20,
                     fg_color=COLORS["border"]).pack(side="left", padx=14)

        ctk.CTkLabel(info_inner, text="PUBLIC IP",
                     text_color=COLORS["text_muted"],
                     font=(UI_S, 8)).pack(side="left", padx=(0, 8))

        self.public_ip_label = ctk.CTkLabel(
            info_inner, text="…", text_color=COLORS["text_dim"],
            font=("Consolas", 11),
        )
        self.public_ip_label.pack(side="left", padx=(0, 2))

        self.public_ip_copy = ctk.CTkButton(
            info_inner, text="📋", command=self._copy_public_ip,
            width=28, height=28, corner_radius=6,
            fg_color="transparent", hover_color=COLORS["surface_3"],
            text_color=COLORS["text_dim"], font=(UI, 11),
        )
        self.public_ip_copy.pack(side="left")

        self.refresh_ip_btn = ctk.CTkButton(
            info_inner, text="↻  Refresh", command=self.refresh_own_ips,
            width=120, height=30, corner_radius=6,
            fg_color=COLORS["surface_3"], hover_color=COLORS["surface_4"],
            text_color=COLORS["text"],
            border_width=1, border_color=COLORS["border"],
            font=(UI_S, 9),
        )
        self.refresh_ip_btn.pack(side="right")

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkLabel(row, text="IPv4 ADDRESS", text_color=COLORS["text_dim"],
                     font=(UI_S, 9)).pack(side="left")

        self.ip_var = tk.StringVar()
        self.ip_entry = ctk.CTkEntry(
            row, textvariable=self.ip_var, width=200, height=40,
            corner_radius=8, border_width=1,
            border_color=COLORS["border"],
            fg_color=COLORS["surface_2"],
            text_color=COLORS["text"], font=(UI, 11),
        )
        self.ip_entry.pack(side="left", padx=(12, 4))

        self.ip_copy_btn = ctk.CTkButton(
            row, text="📋", command=self._copy_entry_ip,
            width=40, height=40, corner_radius=8,
            fg_color=COLORS["surface_2"], hover_color=COLORS["surface_3"],
            text_color=COLORS["text_dim"],
            border_width=1, border_color=COLORS["border"], font=(UI, 14),
        )
        self.ip_copy_btn.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(row, text="/24", text_color=COLORS["text_muted"],
                     font=(UI_S, 10)).pack(side="left")

        self.scan_button = ctk.CTkButton(
            row, text="▶  Start Network Scan", command=self.start_scan,
            width=190, height=40, corner_radius=8,
            fg_color=COLORS["blue"], hover_color=COLORS["blue_dark"],
            font=(UI_S, 10),
        )
        self.scan_button.pack(side="right")

        self.stop_button = ctk.CTkButton(
            row, text="■  Cancel", command=self.stop_scan,
            width=100, height=40, corner_radius=8,
            fg_color=COLORS["surface_2"], hover_color=COLORS["surface_3"],
            text_color=COLORS["text_dim"],
            border_width=1, border_color=COLORS["border"],
            font=(UI_S, 10), state="disabled",
        )
        self.stop_button.pack(side="right", padx=(0, 10))

        self.quick_ping_button = ctk.CTkButton(
            row, text="⚡  Quick Ping", command=self.quick_ping_selected,
            width=130, height=40, corner_radius=8,
            fg_color=COLORS["surface_3"], hover_color=COLORS["surface_4"],
            text_color=COLORS["text"],
            border_width=1, border_color=COLORS["border"],
            font=(UI_S, 10),
        )
        self.quick_ping_button.pack(side="right", padx=(0, 10))

        self.network_hint = ctk.CTkLabel(
            card, text="", text_color=COLORS["text_muted"], font=(UI, 9),
        )
        self.network_hint.pack(anchor="w", padx=20, pady=(0, 16))

    def _build_kpi_row(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=14)

        self.kpi_total   = self._kpi(row, "TOTAL HOSTS", "254")
        self.kpi_online  = self._kpi(row, "ONLINE", "0", accent=COLORS["green"])
        self.kpi_scanned = self._kpi(row, "SCANNED", "0")
        self.kpi_latency = self._kpi(row, "AVG LATENCY", "—")
        self.kpi_time    = self._kpi(row, "ELAPSED", "00.0s")

    def _kpi(self, parent, label, value, accent=None):
        wrapper, card = self._card(parent)
        wrapper.pack(side="left", fill="x", expand=True, padx=(0, 10))
        wrapper.configure(height=100)

        ctk.CTkFrame(card, height=2, corner_radius=2,
                     fg_color=accent or COLORS["blue"]
                     ).pack(fill="x", padx=14, pady=(12, 8))

        ctk.CTkLabel(card, text=label, text_color=COLORS["text_muted"],
                     font=(UI_S, 8)).pack(anchor="w", padx=15, pady=(0, 1))

        value_label = ctk.CTkLabel(card, text=value, text_color=COLORS["text"],
                                   font=(UI_S, 19))
        value_label.pack(anchor="w", padx=15, pady=(0, 14))
        return value_label

    def _build_progress_card(self, parent):
        wrapper, card = self._card(parent)
        wrapper.pack(fill="x")
        wrapper.configure(height=78)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(14, 4))

        self.progress_text = ctk.CTkLabel(
            row, text="Ready", text_color=COLORS["text_dim"], font=(UI, 9),
        )
        self.progress_text.pack(side="left")

        self.progress_percent = ctk.CTkLabel(
            row, text="0%", text_color=COLORS["text_dim"], font=(UI_S, 9),
        )
        self.progress_percent.pack(side="right")

        self.progress = ctk.CTkProgressBar(
            card, height=8, corner_radius=5,
            fg_color=COLORS["surface_2"], progress_color=COLORS["blue"],
        )
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20, pady=(0, 16))

    # ------------------------------------------------------------------
    def _build_results_card(self, parent):
        wrapper, card = self._card(parent)
        wrapper.pack(fill="both", expand=True, pady=(14, 0))

        # -------- toolbar --------------------------------------------
        toolbar = ctk.CTkFrame(card, fg_color="transparent")
        toolbar.pack(fill="x", padx=18, pady=(14, 10))

        ctk.CTkLabel(toolbar, text="Discovered Hosts",
                     text_color=COLORS["text"],
                     font=(UI_S, 13)).pack(side="left")

        self.result_status = ctk.CTkLabel(
            toolbar, text="No scan running", text_color=COLORS["text_muted"],
            font=(UI, 9),
        )
        self.result_status.pack(side="left", padx=(14, 0))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())

        self.search_entry = ctk.CTkEntry(
            toolbar, textvariable=self.search_var,
            placeholder_text="🔎  Search IP, hostname, latency, status...",
            width=300, height=36, corner_radius=8,
            border_width=1, border_color=COLORS["border"],
            fg_color=COLORS["surface_2"], text_color=COLORS["text"],
            placeholder_text_color=COLORS["text_muted"], font=(UI, 9),
        )
        self.search_entry.pack(side="right", padx=(10, 0))

        ctk.CTkButton(
            toolbar, text="📋  Copy All", command=self.copy_all_results,
            width=104, height=36, corner_radius=8,
            fg_color=COLORS["surface_2"], hover_color=COLORS["surface_3"],
            text_color=COLORS["text"],
            border_width=1, border_color=COLORS["border"], font=(UI_S, 9),
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            toolbar, text="⬇  Export", command=self.export_menu,
            width=94, height=36, corner_radius=8,
            fg_color=COLORS["surface_2"], hover_color=COLORS["surface_3"],
            text_color=COLORS["text"],
            border_width=1, border_color=COLORS["border"], font=(UI_S, 9),
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            toolbar, text="🧭  Traceroute", command=self.traceroute_selected,
            width=120, height=36, corner_radius=8,
            fg_color=COLORS["surface_3"], hover_color=COLORS["surface_4"],
            text_color=COLORS["text"],
            border_width=1, border_color=COLORS["border"], font=(UI_S, 9),
        ).pack(side="right", padx=(6, 0))

        # -------- table shell ----------------------------------------
        shell_outer = tk.Frame(card, bg=COLORS["surface"])
        shell_outer.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        shell_border = ctk.CTkFrame(
            shell_outer, fg_color="transparent",
            border_width=1, border_color=COLORS["border"],
            corner_radius=8,
        )
        shell_border.pack(fill="both", expand=True)

        inner = tk.Frame(shell_border, bg=COLORS["surface_2"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self._table_host = inner

        columns = ("ip", "hostname", "latency", "status", "copy")
        self.tree = ttk.Treeview(
            inner, columns=columns, show="headings",
            style="Enterprise.Treeview", selectmode="browse",
        )

        headings = {
            "ip": "IP ADDRESS", "hostname": "HOSTNAME", "latency": "LATENCY",
            "status": "STATUS", "copy": "ACTION",
        }
        widths = {
            "ip": 190, "hostname": 360, "latency": 140,
            "status": 150, "copy": 120,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column], anchor="center")
            self.tree.column(
                column, width=widths[column], anchor="center",
                stretch=column in {"hostname"},
                minwidth=widths[column] - 20,
            )

        self.scrollbar = ttk.Scrollbar(
            inner, orient="vertical", command=self.tree.yview,
            style="Vertical.TScrollbar",
        )
        self.tree.configure(yscrollcommand=self.scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-1>", self._tree_double_click)

        self.tree.tag_configure("excellent", foreground=COLORS["green"])
        self.tree.tag_configure("average",   foreground=COLORS["yellow"])
        self.tree.tag_configure("slow",      foreground=COLORS["red"])
        self.tree.tag_configure("row_alt",   background=COLORS["row_alt"])

        # -------- state overlay --------------------------------------
        # Plain tk.Frame so `place()` accepts explicit width/height.
        # Sits on `inner`, positioned *below* the tree's heading row,
        # so the header (IP ADDRESS / HOSTNAME / …) is never covered.
        self._state_overlay = tk.Frame(inner, bg=COLORS["surface_2"])
        # Not placed yet; state methods will do it.

        # Follow every resize so the overlay stays glued to the body.
        inner.bind("<Configure>", self._on_table_resize, add="+")

    # ==================================================================
    # State overlay (loading ↔ empty — never both at once)
    # ==================================================================
    def _clear_state_overlay(self):
        """Wipe the overlay's children and cancel any running spinner."""
        if self._loading_after_id is not None:
            try:
                self.after_cancel(self._loading_after_id)
            except Exception:
                pass
            self._loading_after_id = None

        if self._state_overlay is None:
            return
        for child in self._state_overlay.winfo_children():
            child.destroy()

    def _place_state_overlay_now(self):
        """Position the overlay strictly inside the body region."""
        if self._state_overlay is None or self._table_host is None:
            return
        try:
            w = self._table_host.winfo_width()
            h = self._table_host.winfo_height()
        except tk.TclError:
            return
        if w <= 1 or h <= 1:
            # layout not ready yet — retry shortly
            self.after(60, self._place_state_overlay_now)
            return
        body_y = HEADER_PX
        body_h = max(0, h - body_y)
        try:
            self._state_overlay.place(
                x=0, y=body_y, width=w, height=body_h,
            )
        except tk.TclError:
            pass

    def _on_table_resize(self, event=None):
        """Keep the overlay aligned with the body when the table resizes."""
        if self._state_mode not in ("loading", "empty"):
            return
        if event is not None and (event.width <= 1 or event.height <= 1):
            return
        self._place_state_overlay_now()

    def _show_loading_state(self, total, network_label=""):
        self._state_mode = "loading"
        self._clear_state_overlay()

        # content box — expanded so its contents center in the body area
        box = ctk.CTkFrame(self._state_overlay, fg_color="transparent")
        box.pack(expand=True)

        ctk.CTkLabel(
            box, text="Scanning Network",
            text_color=COLORS["text"], font=(UI_S, 15),
        ).pack()

        subtitle = (
            f"Probing {total} hosts on {network_label}."
            if network_label else f"Probing {total} hosts."
        ) + "  Results appear as they respond."

        ctk.CTkLabel(
            box, text=subtitle,
            text_color=COLORS["text_muted"], font=(UI, 9),
        ).pack(pady=(6, 22))

        self._spinner_label = ctk.CTkLabel(
            box, text="●   ○   ○   ○",
            text_color=COLORS["blue"], font=(UI, 15),
        )
        self._spinner_label.pack()

        self._loading_frame_idx = 0
        self._animate_loading_spinner()

        self._place_state_overlay_now()

    def _animate_loading_spinner(self):
        if self._state_mode != "loading":
            return
        frames = [
            "●   ○   ○   ○",
            "○   ●   ○   ○",
            "○   ○   ●   ○",
            "○   ○   ○   ●",
        ]
        try:
            self._spinner_label.configure(
                text=frames[self._loading_frame_idx % len(frames)]
            )
        except tk.TclError:
            return
        self._loading_frame_idx += 1
        self._loading_after_id = self.after(220, self._animate_loading_spinner)

    def _show_empty_state(self, filtered=False):
        self._state_mode = "empty"
        self._clear_state_overlay()

        box = ctk.CTkFrame(self._state_overlay, fg_color="transparent")
        box.pack(expand=True)

        ctk.CTkLabel(
            box, text="◌",
            text_color=COLORS["text_muted"], font=(UI, 42),
        ).pack()

        if filtered:
            title = "No matching hosts"
            hint  = "Adjust or clear the search filter to see all results."
        else:
            title = "No hosts discovered"
            hint  = "Start a network scan to populate the results."

        ctk.CTkLabel(
            box, text=title,
            text_color=COLORS["text_dim"], font=(UI_S, 14),
        ).pack(pady=(8, 0))

        ctk.CTkLabel(
            box, text=hint,
            text_color=COLORS["text_muted"], font=(UI, 10),
        ).pack(pady=(6, 0))

        self._place_state_overlay_now()

    def _hide_state_overlay(self):
        self._state_mode = "hidden"
        self._clear_state_overlay()
        if self._state_overlay is not None:
            try:
                self._state_overlay.place_forget()
            except tk.TclError:
                pass

    # ==================================================================
    # Network helpers
    # ==================================================================
    def _detect_network(self):
        try:
            local_ip = self._get_local_ip()
            self.ip_var.set(local_ip)
            self.local_ip_value = local_ip
            self._last_auto_ip = local_ip
            network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
            self.network_hint.configure(
                text=f"Detected subnet: {network}   •   254 usable host addresses"
            )
        except Exception:
            self.ip_var.set("192.168.1.1")
            self.network_hint.configure(
                text="Unable to detect the local network automatically."
            )

        self.after(200, self.refresh_own_ips)

    @staticmethod
    def _get_local_ip():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()

    @staticmethod
    def _get_public_ip(timeout=6):
        endpoints = (
            "https://api.ipify.org",
            "https://ifconfig.me/ip",
            "https://icanhazip.com",
        )
        for url in endpoints:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "NetworkOpsConsole/1.0"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    value = resp.read(64).decode("utf-8", errors="ignore").strip()
                    if value:
                        return value
            except Exception:
                continue
        return None

    def _ping(self, ip, timeout_ms=500):
        system = platform.system().lower()

        if system == "windows":
            command = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
            creationflags = subprocess.CREATE_NO_WINDOW
        else:
            command = ["ping", "-c", "1", "-W",
                       str(max(1, timeout_ms // 1000)), ip]
            creationflags = 0

        started = time.perf_counter()

        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, creationflags=creationflags,
                timeout=max(1.5, timeout_ms / 1000 + 0.7),
            )
            if result.returncode != 0:
                return None

            latency = (time.perf_counter() - started) * 1000

            try:
                hostname = socket.gethostbyaddr(ip)[0]
            except (socket.herror, socket.gaierror, OSError):
                hostname = "—"

            return {
                "ip": ip, "hostname": hostname,
                "latency_ms": round(latency, 1),
                "status": self._latency_status(latency),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        except (subprocess.TimeoutExpired, OSError):
            return None

    @staticmethod
    def _latency_status(latency):
        if latency <= 50:
            return "Excellent"
        if latency <= 150:
            return "Average"
        return "High"

    # ==================================================================
    # Own-IP refresh
    # ==================================================================
    def refresh_own_ips(self):
        if self._ip_refresh_active:
            return
        self._ip_refresh_active = True

        self.refresh_ip_btn.configure(state="disabled")
        self.local_ip_label.configure(text="…", text_color=COLORS["text_muted"])
        self.public_ip_label.configure(text="…", text_color=COLORS["text_muted"])
        self.local_ip_copy.configure(state="disabled")
        self.public_ip_copy.configure(state="disabled")

        self._refresh_dot_count = 0
        self._animate_refresh_button()

        threading.Thread(target=self._refresh_ips_worker, daemon=True).start()

    def _animate_refresh_button(self):
        if not self._ip_refresh_active:
            return
        dots = "." * (self._refresh_dot_count % 4)
        try:
            self.refresh_ip_btn.configure(text=f"↻  Refreshing{dots}")
        except tk.TclError:
            return
        self._refresh_dot_count += 1
        self.after(400, self._animate_refresh_button)

    def _refresh_ips_worker(self):
        try:
            local_ip = self._get_local_ip()
        except Exception:
            local_ip = None
        public_ip = self._get_public_ip(timeout=6)
        self.event_queue.put(("own_ips", (local_ip, public_ip)))

    def _apply_own_ips(self, local_ip, public_ip):
        self._ip_refresh_active = False

        if local_ip:
            self.local_ip_value = local_ip
            self.local_ip_label.configure(text=local_ip,
                                          text_color=COLORS["text"])
            self.local_ip_copy.configure(state="normal")

            current = self.ip_var.get().strip()
            if (not current) or (current == self._last_auto_ip):
                self.ip_var.set(local_ip)
            self._last_auto_ip = local_ip

            network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
            self.network_hint.configure(
                text=f"Detected subnet: {network}   •   254 usable host addresses"
            )
        else:
            self.local_ip_value = None
            self.local_ip_label.configure(text="Unavailable",
                                          text_color=COLORS["text_muted"])
            self.local_ip_copy.configure(state="disabled")

        if public_ip:
            self.public_ip_value = public_ip
            self.public_ip_label.configure(text=public_ip,
                                           text_color=COLORS["text"])
            self.public_ip_copy.configure(state="normal")
        else:
            self.public_ip_value = None
            self.public_ip_label.configure(text="Unavailable",
                                           text_color=COLORS["text_muted"])
            self.public_ip_copy.configure(state="disabled")

        self.refresh_ip_btn.configure(state="normal", text="↻  Refresh")

        if local_ip and public_ip:
            self._toast("Network information refreshed.", "success")
        elif local_ip:
            self._toast("Local IP refreshed · public IP unavailable.", "warning")
        else:
            self._toast("Unable to refresh network information.", "error")

    def _copy_local_ip(self):
        if not self.local_ip_value:
            self._toast("No local IP to copy.", "warning")
            return
        self._copy_to_clipboard(self.local_ip_value)
        self._flash_copy_button(self.local_ip_copy)
        self._toast(f"✓ Copied {self.local_ip_value}", "success")

    def _copy_public_ip(self):
        if not self.public_ip_value:
            self._toast("No public IP to copy.", "warning")
            return
        self._copy_to_clipboard(self.public_ip_value)
        self._flash_copy_button(self.public_ip_copy)
        self._toast(f"✓ Copied {self.public_ip_value}", "success")

    def _flash_copy_button(self, button):
        try:
            original = button.cget("text")
        except tk.TclError:
            return
        try:
            button.configure(text="✓", text_color=COLORS["green"])
        except tk.TclError:
            return

        def restore():
            try:
                if button.winfo_exists():
                    button.configure(text=original,
                                     text_color=COLORS["text_dim"])
            except tk.TclError:
                pass

        self.after(1400, restore)

    # ==================================================================
    # Scan lifecycle
    # ==================================================================
    def start_scan(self):
        if self.scan_thread and self.scan_thread.is_alive():
            return

        raw_ip = self.ip_var.get().strip()

        try:
            address = ipaddress.ip_address(raw_ip)
            network = ipaddress.ip_network(f"{address}/24", strict=False)
        except ValueError:
            self._set_error("Invalid IPv4 address.")
            return

        self.stop_event.clear()
        self.results.clear()
        self.filtered_results.clear()
        self.scanned_count = 0
        self.online_count = 0
        self.scan_started_at = time.perf_counter()

        for item in self.tree.get_children():
            self.tree.delete(item)

        hosts = [str(host) for host in network.hosts()]
        self.total_hosts = len(hosts)

        self.kpi_total.configure(text=str(self.total_hosts))
        self.kpi_online.configure(text="0")
        self.kpi_scanned.configure(text="0")
        self.kpi_latency.configure(text="—")
        self.progress.set(0)
        self.progress_text.configure(
            text=f"Scanning {self.total_hosts} hosts on {network}..."
        )
        self.progress_percent.configure(text="0%")
        self.result_status.configure(text="Scan in progress")
        self.header_status.configure(text="●  SCANNING",
                                     text_color=COLORS["yellow"])

        self.scan_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

        # replace any existing empty state with the loading state
        self._show_loading_state(self.total_hosts, str(network))

        self._toast(f"Scanning {network} using parallel ICMP requests.", "info")

        self.scan_thread = threading.Thread(
            target=self._scan_worker, args=(hosts,), daemon=True,
        )
        self.scan_thread.start()

    def _scan_worker(self, hosts):
        workers = min(64, max(8, len(hosts)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self._ping, ip): ip for ip in hosts}
            for future in concurrent.futures.as_completed(futures):
                if self.stop_event.is_set():
                    break
                result = future.result()
                self.event_queue.put(("progress", result))
        self.event_queue.put(("finished", None))

    def _process_events(self):
        try:
            while True:
                event, payload = self.event_queue.get_nowait()

                if event == "progress":
                    self.scanned_count += 1
                    if payload:
                        self.results.append(payload)
                        self.online_count += 1
                        self._insert_result(payload)
                    self._update_scan_progress()

                elif event == "own_ips":
                    local_ip, public_ip = payload
                    self._apply_own_ips(local_ip, public_ip)

                elif event == "finished":
                    self._scan_finished()

                elif event == "quick_ping":
                    if payload and "latency_ms" in payload:
                        self._toast(
                            f'{payload["ip"]}  ·  '
                            f'{payload.get("hostname", "—")}  ·  '
                            f'{payload["latency_ms"]:.1f} ms',
                            "success",
                        )
                    else:
                        self._toast(f'{payload["ip"]} did not respond.', "error")

                elif event == "traceroute":
                    target, output = payload
                    self._show_traceroute(target, output)

                elif event == "traceroute_error":
                    target, error = payload
                    self._toast(f"Traceroute failed for {target}: {error}",
                                "error")

        except queue.Empty:
            pass
        self.after(100, self._process_events)

    def _update_scan_progress(self):
        progress = self.scanned_count / max(1, self.total_hosts)
        self.progress.set(progress)
        self.progress_percent.configure(text=f"{progress * 100:.0f}%")
        self.kpi_scanned.configure(text=str(self.scanned_count))
        self.kpi_online.configure(text=str(self.online_count))

        if self.results:
            avg = sum(x["latency_ms"] for x in self.results) / len(self.results)
            self.kpi_latency.configure(text=f"{avg:.1f} ms")
            # first result → hide the loading overlay
            if self._state_mode == "loading":
                self._hide_state_overlay()

        if self.scan_started_at:
            elapsed = time.perf_counter() - self.scan_started_at
            self.kpi_time.configure(text=f"{elapsed:.1f}s")

    def _scan_finished(self):
        elapsed = time.perf_counter() - self.scan_started_at

        if self.stop_event.is_set():
            self.header_status.configure(text="●  CANCELLED",
                                         text_color=COLORS["red"])
            self.progress_text.configure(text="Scan cancelled")
            self.result_status.configure(text="Scan cancelled")
            self._toast("Network scan cancelled.", "warning")
        else:
            self.header_status.configure(text="●  READY",
                                         text_color=COLORS["green"])
            self.progress.set(1)
            self.progress_percent.configure(text="100%")
            self.progress_text.configure(
                text=f"Completed in {elapsed:.1f}s  •  "
                     f"{self.online_count} online host(s)"
            )
            self.result_status.configure(
                text=f"Completed  •  {self.online_count} online"
            )
            self._toast(
                f"Scan completed: {self.online_count} online host(s).",
                "success",
            )

        self.scan_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

        self._apply_filter()
        if not self.results:
            self._show_empty_state(filtered=False)

    def stop_scan(self):
        if not (self.scan_thread and self.scan_thread.is_alive()):
            return
        self.stop_event.set()
        self.header_status.configure(text="●  CANCELLING",
                                     text_color=COLORS["yellow"])
        self.result_status.configure(text="Cancelling...")
        self.stop_button.configure(state="disabled")
        self._toast("Stopping active scan...", "warning")

    # ==================================================================
    # Table rendering
    # ==================================================================
    def _insert_result(self, result):
        tag = {"Excellent": "excellent", "Average": "average",
               "High": "slow"}.get(result["status"], "average")

        index = len(self.tree.get_children())
        tags = (tag,)
        if index % 2 == 1:
            tags = (tag, "row_alt")

        self.tree.insert(
            "", "end", iid=result["ip"],
            values=(
                result["ip"], result["hostname"],
                f'{result["latency_ms"]:.1f} ms',
                result["status"], "📋  Copy",
            ),
            tags=tags,
        )

    def _apply_filter(self):
        query = self.search_var.get().strip().lower()

        for item in self.tree.get_children():
            self.tree.delete(item)

        visible = []
        for result in self.results:
            haystack = (
                f'{result["ip"]} {result["hostname"]} '
                f'{result["latency_ms"]} {result["status"]}'
            ).lower()
            if not query or query in haystack:
                visible.append(result)
                self._insert_result(result)

        self.filtered_results = visible

        # leave loading overlay alone while scanning
        if self.scan_thread and self.scan_thread.is_alive():
            return

        if visible:
            self._hide_state_overlay()
        else:
            self._show_empty_state(filtered=bool(query))

    def _selected_result(self):
        selection = self.tree.selection()
        if not selection:
            return None
        ip = selection[0]
        return next((r for r in self.results if r["ip"] == ip), None)

    def _tree_click(self, event):
        row_id = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row_id or not column:
            return

        result = next((r for r in self.results if r["ip"] == row_id), None)
        if not result:
            return

        col_index = int(column.replace("#", "")) - 1
        columns = ("ip", "hostname", "latency", "status", "copy")
        key = columns[col_index]

        if key == "copy":
            self._copy_to_clipboard(result["ip"])
            self._flash_copy_cell(row_id)
            self._toast(f'✓ Copied  {result["ip"]}', "success")
            return

        if key == "latency":
            text = f'{result["latency_ms"]:.1f} ms'
        elif key == "hostname":
            text = result["hostname"] if result["hostname"] != "—" else ""
            if not text:
                self._toast("No hostname available for this host.", "warning")
                return
        else:
            text = str(result.get(key, ""))

        if text and text != "—":
            self._copy_to_clipboard(text)
            self._toast(f'✓ Copied: {text}', "success")

    def _tree_double_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        result = next((r for r in self.results if r["ip"] == row_id), None)
        if result:
            self.quick_ping_target(result["ip"])

    def _flash_copy_cell(self, iid):
        try:
            values = list(self.tree.item(iid, "values"))
            if len(values) < 5:
                return
            values[4] = "✓  Copied"
            self.tree.item(iid, values=values)

            def restore():
                try:
                    if self.tree.exists(iid):
                        current = list(self.tree.item(iid, "values"))
                        if len(current) >= 5 and current[4] == "✓  Copied":
                            current[4] = "📋  Copy"
                            self.tree.item(iid, values=current)
                except tk.TclError:
                    pass

            self.after(1500, restore)
        except tk.TclError:
            pass

    # ==================================================================
    # Clipboard
    # ==================================================================
    def _copy_to_clipboard(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def _copy_entry_ip(self):
        value = self.ip_var.get().strip()
        if not value:
            self._toast("No address to copy.", "warning")
            return
        self._copy_to_clipboard(value)
        self.ip_copy_btn.configure(text="✓", text_color=COLORS["green"])
        self.after(1400, lambda: self.ip_copy_btn.configure(
            text="📋", text_color=COLORS["text_dim"]))
        self._toast(f"✓ Copied {value}", "success")

    def copy_selected(self):
        result = self._selected_result()
        if not result:
            self._toast("Select a host first.", "warning")
            return
        self._copy_to_clipboard(result["ip"])
        self._toast(f'✓ Copied {result["ip"]}', "success")

    def copy_all_results(self):
        if not self.results:
            self._toast("There are no results to copy.", "warning")
            return
        text = "\n".join(
            f'{r["ip"]}\t{r["hostname"]}\t{r["latency_ms"]:.1f} ms\t{r["status"]}'
            for r in self.results
        )
        self._copy_to_clipboard(text)
        self._toast(f"✓ Copied {len(self.results)} result(s) to clipboard.",
                    "success")

    # ==================================================================
    # Quick ping / traceroute
    # ==================================================================
    def quick_ping_selected(self):
        result = self._selected_result()
        if not result:
            raw = self.ip_var.get().strip()
            try:
                ipaddress.ip_address(raw)
                target = raw
            except ValueError:
                self._toast("Select a host or enter a valid IPv4 address.",
                            "warning")
                return
        else:
            target = result["ip"]
        self.quick_ping_target(target)

    def quick_ping_target(self, target):
        self._toast(f"⚡ Pinging {target}...", "info")
        threading.Thread(target=self._quick_ping_worker,
                         args=(target,), daemon=True).start()

    def _quick_ping_worker(self, target):
        result = self._ping(target, timeout_ms=1000)
        self.event_queue.put(("quick_ping", result or {"ip": target}))

    def traceroute_selected(self):
        result = self._selected_result()
        target = result["ip"] if result else self.ip_var.get().strip()
        try:
            ipaddress.ip_address(target)
        except ValueError:
            self._toast("Enter or select a valid IPv4 address.", "warning")
            return
        self._toast(f"🧭 Running traceroute to {target}...", "info")
        threading.Thread(target=self._traceroute_worker,
                         args=(target,), daemon=True).start()

    def _traceroute_worker(self, target):
        system = platform.system().lower()
        if system == "windows":
            command = ["tracert", "-d", "-w", "700", target]
        else:
            command = ["traceroute", "-n", "-w", "1", target]
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=35,
                creationflags=subprocess.CREATE_NO_WINDOW
                if system == "windows" else 0,
            )
            self.event_queue.put(("traceroute", (target, result.stdout.strip())))
        except Exception as exc:
            self.event_queue.put(("traceroute_error", (target, str(exc))))

    def _show_traceroute(self, target, output):
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Traceroute  |  {target}")
        dialog.geometry("880x600")
        dialog.minsize(700, 460)
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(self)

        self.after(80, lambda: apply_app_icon(dialog, self._app_pil_img))

        ctk.CTkLabel(dialog, text=f"Traceroute to {target}",
                     text_color=COLORS["text"],
                     font=(UI_S, 15)).pack(anchor="w", padx=22, pady=(18, 2))

        ctk.CTkLabel(dialog, text="Diagnostic output",
                     text_color=COLORS["text_muted"],
                     font=(UI, 9)).pack(anchor="w", padx=22, pady=(0, 12))

        text = ctk.CTkTextbox(
            dialog, fg_color=COLORS["surface_2"], text_color="#B8F1D0",
            border_width=1, border_color=COLORS["border"],
            corner_radius=10, font=MONO,
        )
        text.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        text.insert("1.0", output or "No traceroute output.")
        text.configure(state="disabled")

        row = ctk.CTkFrame(dialog, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 18))

        def copy_output():
            self._copy_to_clipboard(output or "")
            self._toast("✓ Traceroute output copied.", "success")

        ctk.CTkButton(row, text="📋  Copy Output", command=copy_output,
                      width=140, height=36, corner_radius=8,
                      fg_color=COLORS["blue"], hover_color=COLORS["blue_dark"],
                      font=(UI_S, 9)).pack(side="right")

        ctk.CTkButton(row, text="Close", command=dialog.destroy,
                      width=96, height=36, corner_radius=8,
                      fg_color=COLORS["surface_2"],
                      hover_color=COLORS["surface_3"],
                      text_color=COLORS["text"],
                      border_width=1, border_color=COLORS["border"],
                      font=(UI_S, 9)).pack(side="right", padx=(0, 8))

    # ==================================================================
    # Export
    # ==================================================================
    def export_menu(self):
        if not self.results:
            self._toast("There are no results to export.", "warning")
            return
        menu = tk.Menu(
            self, tearoff=False,
            bg=COLORS["surface_2"], fg=COLORS["text"],
            activebackground=COLORS["blue_soft"],
            activeforeground=COLORS["white"],
            font=(UI, 9), borderwidth=0,
        )
        menu.add_command(label="⬇  Export as CSV",  command=self.export_csv)
        menu.add_command(label="⬇  Export as JSON", command=self.export_json)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def export_csv(self):
        path = filedialog.asksaveasfilename(
            title="Export network results", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="network_scan_results.csv",
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=["ip", "hostname", "latency_ms",
                               "status", "timestamp"])
            writer.writeheader()
            writer.writerows(self.results)
        self._toast("✓ CSV export completed.", "success")

    def export_json(self):
        path = filedialog.asksaveasfilename(
            title="Export network results", defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile="network_scan_results.json",
        )
        if not path:
            return
        payload = {
            "application": APP_NAME, "version": APP_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "local_ipv4": self.ip_var.get().strip(),
            "results": self.results, "result_count": len(self.results),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self._toast("✓ JSON export completed.", "success")

    # ==================================================================
    # Shortcuts / misc
    # ==================================================================
    def _bind_shortcuts(self):
        self.bind_all("<Control-c>", lambda e: self.copy_selected())
        self.bind_all("<Return>",    lambda e: self._enter_action())
        self.bind_all("<Escape>",    lambda e: self.stop_scan())
        self.bind_all("<Control-f>", lambda e: self.search_entry.focus_set())
        self.bind_all("<Control-e>", lambda e: self.export_menu())
        self.bind_all("<Control-r>", lambda e: self.refresh_own_ips())

    def _enter_action(self):
        if self.scan_thread and self.scan_thread.is_alive():
            return
        self.quick_ping_selected()

    def _refresh_clock(self):
        if self.scan_started_at and self.scan_thread \
                and self.scan_thread.is_alive():
            elapsed = time.perf_counter() - self.scan_started_at
            self.kpi_time.configure(text=f"{elapsed:.1f}s")
        self.after(1000, self._refresh_clock)

    def _set_error(self, message):
        self.header_status.configure(text="●  ERROR", text_color=COLORS["red"])
        self._toast(message, "error")

    # ==================================================================
    # Toast system
    # ==================================================================
    @staticmethod
    def _toast_accent(level):
        return {"success": COLORS["green"], "warning": COLORS["yellow"],
                "error": COLORS["red"], "info": COLORS["blue"]
                }.get(level, COLORS["blue"])

    @staticmethod
    def _lerp(c1, c2, t):
        t = max(0.0, min(1.0, t))
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        return "#{:02X}{:02X}{:02X}".format(
            int(r1 + (r2 - r1) * t),
            int(g1 + (g2 - g1) * t),
            int(b1 + (b2 - b1) * t),
        )

    @staticmethod
    def _widget_alive(w):
        try:
            return bool(w.winfo_exists())
        except tk.TclError:
            return False

    def _safe_config(self, widget, **kwargs):
        try:
            if widget and self._widget_alive(widget):
                widget.configure(**kwargs)
        except tk.TclError:
            pass

    def _toast(self, message, level="info"):
        accent = self._toast_accent(level)

        container = ctk.CTkFrame(
            self, fg_color="transparent", width=TOAST_W, height=TOAST_H,
        )

        for dy, radius in ((4, 14), (3, 13), (2, 12)):
            ctk.CTkFrame(container, fg_color=COLORS["shadow"],
                         corner_radius=radius,
                         width=TOAST_W, height=TOAST_H).place(x=0, y=dy)

        surface = ctk.CTkFrame(
            container, fg_color=COLORS["surface"], corner_radius=11,
            border_width=1, border_color=COLORS["border"],
            width=TOAST_W, height=TOAST_H,
        )
        surface.place(x=0, y=0)

        ctk.CTkFrame(
            surface, fg_color=accent, corner_radius=2,
            width=4, height=TOAST_H - 22,
        ).place(x=16, y=11)

        msg = ctk.CTkLabel(
            surface, text=message, text_color=COLORS["surface"],
            font=(UI, 10), anchor="w", justify="left",
            wraplength=TOAST_W - 60,
        )
        msg.place(x=34, rely=0.5, anchor="w")

        container._surface = surface
        container._msg = msg

        self._toasts.append(container)
        self._reposition_toasts(animated_container=container)

        self.after(TOAST_DURATION, lambda c=container: self._remove_toast(c))

    def _reposition_toasts(self, animated_container=None):
        for i, toast in enumerate(self._toasts):
            if not self._widget_alive(toast):
                continue
            target_y = TOAST_TOP + i * (TOAST_H + TOAST_GAP)
            if toast is animated_container:
                start_y = -TOAST_H - 24
                self._slide_toast(toast, start_y, target_y)
            else:
                toast.place(relx=0.5, rely=0.0, anchor="n", x=0, y=target_y)

    def _slide_toast(self, toast, start_y, end_y):
        total_frames = max(1, TOAST_ANIM_MS // TOAST_FRAME_MS)
        surface = getattr(toast, "_surface", None)
        msg     = getattr(toast, "_msg", None)
        s0, s1 = COLORS["surface"], COLORS["surface_2"]
        t0, t1 = COLORS["surface_2"], COLORS["text"]

        def step(i=0):
            if not self._widget_alive(toast):
                return
            if i >= total_frames:
                toast.place(relx=0.5, rely=0.0, anchor="n", x=0, y=end_y)
                self._safe_config(surface, fg_color=s1)
                self._safe_config(msg, text_color=t1)
                return
            t = i / total_frames
            eased = 1 - (1 - t) ** 3
            y = int(start_y + (end_y - start_y) * eased)
            toast.place(relx=0.5, rely=0.0, anchor="n", x=0, y=y)
            self._safe_config(surface, fg_color=self._lerp(s0, s1, t))
            self._safe_config(msg, text_color=self._lerp(t0, t1, t))
            self.after(TOAST_FRAME_MS, lambda: step(i + 1))

        step(0)

    def _remove_toast(self, container):
        if container not in self._toasts:
            return
        if not self._widget_alive(container):
            self._toasts.remove(container)
            return

        try:
            current_y = int(container.place_info().get("y", TOAST_TOP))
        except (tk.TclError, ValueError):
            current_y = TOAST_TOP

        end_y = current_y - 18
        total_frames = 9
        frame_ms = 14

        surface = getattr(container, "_surface", None)
        msg     = getattr(container, "_msg", None)
        s0, s1 = COLORS["surface_2"], COLORS["surface"]
        t0, t1 = COLORS["text"], COLORS["surface"]

        def step(i=0):
            if not self._widget_alive(container):
                if container in self._toasts:
                    self._toasts.remove(container)
                return
            if i >= total_frames:
                if container in self._toasts:
                    self._toasts.remove(container)
                try:
                    container.destroy()
                except tk.TclError:
                    pass
                self._reposition_toasts(animated_container=None)
                return
            t = i / total_frames
            eased = t * t
            y = int(current_y + (end_y - current_y) * eased)
            container.place(relx=0.5, rely=0.0, anchor="n", x=0, y=y)
            self._safe_config(surface, fg_color=self._lerp(s0, s1, t))
            self._safe_config(msg, text_color=self._lerp(t0, t1, t))
            self.after(frame_ms, lambda: step(i + 1))

        step(0)


# =====================================================================
# Entry point
# =====================================================================
def main():
    enable_high_dpi()
    set_windows_taskbar_identity(APP_ID)
    app = NetworkScannerApp()
    app.mainloop()


if __name__ == "__main__":
    main()


# =====================================================================
# BUILD INSTRUCTIONS — single-file EXE with PyInstaller
# =====================================================================
#
# 1. Install PyInstaller once:
#       pip install pyinstaller
#
# 2. Provide a single icon asset next to this .py file:
#       app.png     ← square PNG, ideally 512×512 or 1024×1024
#
# 3. Build the executable:
#
#    ── Windows (PowerShell / CMD) ──────────────────────────────────
#       pyinstaller ^
#         --onefile ^
#         --noconsole ^
#         --name "NetworkOpsConsole" ^
#         --icon "app.png" ^
#         --add-data "app.png;." ^
#         --clean ^
#         network_discovery.py
#
#    ── Linux / macOS ───────────────────────────────────────────────
#       pyinstaller \
#         --onefile \
#         --noconsole \
#         --name "NetworkOpsConsole" \
#         --icon "app.png" \
#         --add-data "app.png:." \
#         --clean \
#         network_discovery.py
#
# 4. Result:
#       dist/NetworkOpsConsole.exe    (Windows)
#       dist/NetworkOpsConsole        (Linux/Mac)
#
# =====================================================================