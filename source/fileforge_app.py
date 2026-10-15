#!/usr/bin/env python3
"""Modern desktop UI for FileForge."""

from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

import fileforge


APP_VERSION = "2.0.1"

BG = ("#F3F6FA", "#090D16")
CARD = ("#FFFFFF", "#121824")
CARD_ALT = ("#F7F9FC", "#171F2E")
TEXT = ("#152033", "#F3F6FC")
MUTED = ("#64748B", "#94A3B8")
BORDER = ("#DFE6EF", "#263244")
ACCENT = "#3977F6"
ACCENT_HOVER = "#2E65D7"
SUCCESS = "#19A974"
DANGER = "#E05260"
SIDEBAR = "#0D1525"
SIDEBAR_MUTED = "#93A4BE"

FONT = "Microsoft YaHei UI"

UNIT_TOKENS = {
    "字节 · B": "B",
    "KB · Windows 1024": "KB",
    "MB · Windows 1024²": "MB",
    "GB · Windows 1024³": "GB",
    "TB · Windows 1024⁴": "TB",
    "kB · 十进制 1000": "KB1000",
    "MB · 十进制 1000²": "MB1000",
    "GB · 十进制 1000³": "GB1000",
    "TB · 十进制 1000⁴": "TB1000",
}
DEFAULT_UNIT = "MB · Windows 1024²"

CATEGORY_COLORS = {
    "通用": "#64748B",
    "文本与代码": "#8B5CF6",
    "文档": "#F59E0B",
    "Office": "#2563EB",
    "图片": "#EC4899",
    "音频": "#14B8A6",
    "视频/媒体": "#EF4444",
    "压缩与归档": "#10B981",
    "数据库": "#6366F1",
    "数据交换": "#0EA5E9",
}


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


def windows_size_label(size: int) -> str:
    if size < 1024:
        return f"{size:,} 字节"
    units = ((1024**4, "TB"), (1024**3, "GB"), (1024**2, "MB"), (1024, "KB"))
    for factor, name in units:
        if size >= factor:
            return f"{size / factor:,.2f} {name}"
    return f"{size:,} 字节"


class FileForgeApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"FileForge {APP_VERSION}")
        self.geometry("1040x720")
        self.minsize(920, 660)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        icon = resource_path("assets/fileforge.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass

        self._results: queue.Queue[tuple[str, object, object]] = queue.Queue()
        self._busy = False
        self._last_output: Path | None = None
        self._format_labels = {
            f"{spec.name.upper()}   ·   {spec.description}": spec for spec in fileforge.SPECS
        }

        self.output_var = ctk.StringVar(value=str(Path.home() / "Documents" / "blank.pdf"))
        self.size_var = ctk.StringVar(value="10")
        self.unit_var = ctk.StringVar(value=DEFAULT_UNIT)
        self.format_var = ctk.StringVar(value=self._label_for_format("pdf"))
        self.overwrite_var = ctk.BooleanVar(value=False)
        self.theme_var = ctk.StringVar(value="跟随系统")

        self._build_layout()
        self._bind_updates()
        self._update_format_details()
        self._update_size_preview()

    def _font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family=FONT, size=size, weight=weight)

    def _label_for_format(self, name: str) -> str:
        for label, spec in self._format_labels.items():
            if spec.name == name:
                return label
        return next(iter(self._format_labels))

    def _selected_spec(self) -> fileforge.FormatSpec:
        return self._format_labels[self.format_var.get()]

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self.sidebar = ctk.CTkFrame(self, width=250, corner_radius=0, fg_color=SIDEBAR)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(8, weight=1)

        logo = ctk.CTkFrame(self.sidebar, width=52, height=52, corner_radius=15, fg_color=ACCENT)
        logo.grid(row=0, column=0, padx=24, pady=(28, 14), sticky="w")
        logo.grid_propagate(False)
        ctk.CTkLabel(logo, text="F", font=self._font(26, "bold"), text_color="#FFFFFF").place(relx=0.5, rely=0.48, anchor="center")

        ctk.CTkLabel(self.sidebar, text="FileForge", font=self._font(23, "bold"), text_color="#FFFFFF").grid(row=1, column=0, padx=24, sticky="w")
        ctk.CTkLabel(self.sidebar, text="精确大小文件生成器", font=self._font(12), text_color=SIDEBAR_MUTED).grid(row=2, column=0, padx=24, pady=(2, 28), sticky="w")

        self.create_nav = ctk.CTkButton(
            self.sidebar,
            text="  创建文件",
            anchor="w",
            height=44,
            corner_radius=11,
            font=self._font(14, "bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=lambda: None,
        )
        self.create_nav.grid(row=3, column=0, padx=16, pady=4, sticky="ew")

        self.formats_nav = ctk.CTkButton(
            self.sidebar,
            text="  支持格式",
            anchor="w",
            height=44,
            corner_radius=11,
            font=self._font(14),
            fg_color="transparent",
            hover_color="#19263A",
            text_color="#D7E2F0",
            command=self._show_formats,
        )
        self.formats_nav.grid(row=4, column=0, padx=16, pady=4, sticky="ew")

        stats = ctk.CTkFrame(self.sidebar, corner_radius=14, fg_color="#141F32")
        stats.grid(row=6, column=0, padx=16, pady=(28, 0), sticky="ew")
        ctk.CTkLabel(stats, text=f"{len(fileforge.SPECS)}", font=self._font(25, "bold"), text_color="#FFFFFF").pack(anchor="w", padx=16, pady=(14, 0))
        ctk.CTkLabel(stats, text="类真实文件格式", font=self._font(11), text_color=SIDEBAR_MUTED).pack(anchor="w", padx=16, pady=(0, 12))

        theme_area = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        theme_area.grid(row=9, column=0, padx=18, pady=(10, 22), sticky="sew")
        ctk.CTkLabel(theme_area, text="外观", font=self._font(11), text_color=SIDEBAR_MUTED).pack(anchor="w", padx=4, pady=(0, 7))
        self.theme_menu = ctk.CTkOptionMenu(
            theme_area,
            values=["跟随系统", "浅色", "深色"],
            variable=self.theme_var,
            height=36,
            corner_radius=9,
            font=self._font(12),
            fg_color="#19263A",
            button_color="#253650",
            button_hover_color="#304666",
            command=self._change_theme,
        )
        self.theme_menu.pack(fill="x")
        ctk.CTkLabel(theme_area, text=f"v{APP_VERSION}  ·  本地离线生成", font=self._font(10), text_color="#60738F").pack(anchor="w", padx=4, pady=(12, 0))

        self.content = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew", padx=(0, 4), pady=0)
        self.content.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.grid(row=0, column=0, padx=34, pady=(30, 20), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="创建空白文件", font=self._font(27, "bold"), text_color=TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="选择格式和容量，生成能被对应软件打开的最小内容文件。", font=self._font(12), text_color=MUTED).grid(row=1, column=0, pady=(5, 0), sticky="w")
        badge = ctk.CTkLabel(header, text="  精确到 1 字节  ", height=28, corner_radius=14, fg_color=("#E8F0FF", "#182B50"), text_color=("#2864D7", "#7FACFF"), font=self._font(11, "bold"))
        badge.grid(row=0, column=1, rowspan=2, padx=(16, 0), sticky="e")

        self.path_card = self._card(1)
        self._section_title(self.path_card, "01", "保存位置", "选择最终文件名和保存目录").grid(row=0, column=0, columnspan=2, padx=22, pady=(18, 13), sticky="ew")
        self.path_card.grid_columnconfigure(0, weight=1)
        self.path_entry = ctk.CTkEntry(
            self.path_card,
            textvariable=self.output_var,
            height=44,
            corner_radius=10,
            border_width=1,
            border_color=BORDER,
            fg_color=CARD_ALT,
            text_color=TEXT,
            font=self._font(12),
            placeholder_text="请选择输出文件…",
        )
        self.path_entry.grid(row=1, column=0, padx=(22, 10), pady=(0, 20), sticky="ew")
        ctk.CTkButton(
            self.path_card,
            text="浏览…",
            width=88,
            height=44,
            corner_radius=10,
            fg_color=("#E9EEF6", "#202B3C"),
            hover_color=("#DDE5F0", "#2B394E"),
            text_color=TEXT,
            font=self._font(12, "bold"),
            command=self._browse,
        ).grid(row=1, column=1, padx=(0, 22), pady=(0, 20))

        self.spec_card = self._card(2)
        self._section_title(self.spec_card, "02", "文件规格", "选择真实格式以及目标逻辑大小").grid(row=0, column=0, columnspan=3, padx=22, pady=(18, 13), sticky="ew")
        self.spec_card.grid_columnconfigure(0, weight=3)
        self.spec_card.grid_columnconfigure(1, weight=2)

        ctk.CTkLabel(self.spec_card, text="文件格式", font=self._font(11, "bold"), text_color=MUTED).grid(row=1, column=0, padx=(22, 10), sticky="w")
        ctk.CTkLabel(self.spec_card, text="数值", font=self._font(11, "bold"), text_color=MUTED).grid(row=1, column=1, padx=8, sticky="w")
        ctk.CTkLabel(self.spec_card, text="单位", font=self._font(11, "bold"), text_color=MUTED).grid(row=1, column=2, padx=(8, 22), sticky="w")

        self.format_menu = ctk.CTkComboBox(
            self.spec_card,
            values=list(self._format_labels),
            variable=self.format_var,
            height=44,
            corner_radius=10,
            border_width=1,
            border_color=BORDER,
            fg_color=CARD_ALT,
            button_color=("#E7EDF6", "#243146"),
            button_hover_color=("#D9E3F1", "#30415B"),
            text_color=TEXT,
            dropdown_font=self._font(11),
            font=self._font(12),
            state="readonly",
            command=lambda _value: self._on_format_changed(),
        )
        self.format_menu.grid(row=2, column=0, padx=(22, 10), pady=(7, 10), sticky="ew")

        self.size_entry = ctk.CTkEntry(
            self.spec_card,
            textvariable=self.size_var,
            height=44,
            corner_radius=10,
            border_width=1,
            border_color=BORDER,
            fg_color=CARD_ALT,
            text_color=TEXT,
            font=self._font(14, "bold"),
        )
        self.size_entry.grid(row=2, column=1, padx=8, pady=(7, 10), sticky="ew")

        self.unit_menu = ctk.CTkOptionMenu(
            self.spec_card,
            values=list(UNIT_TOKENS),
            variable=self.unit_var,
            height=44,
            width=180,
            corner_radius=10,
            fg_color=("#E7EDF6", "#243146"),
            button_color=("#D9E3F1", "#30415B"),
            button_hover_color=("#CBD8E9", "#3A4E6C"),
            text_color=TEXT,
            font=self._font(11),
            dropdown_font=self._font(11),
            command=lambda _value: self._update_size_preview(),
        )
        self.unit_menu.grid(row=2, column=2, padx=(8, 22), pady=(7, 10), sticky="ew")

        self.format_detail = ctk.CTkLabel(self.spec_card, text="", font=self._font(11), text_color=MUTED, anchor="w")
        self.format_detail.grid(row=3, column=0, padx=(24, 10), pady=(0, 14), sticky="ew")

        preset_frame = ctk.CTkFrame(self.spec_card, fg_color="transparent")
        preset_frame.grid(row=3, column=1, columnspan=2, padx=(8, 22), pady=(0, 14), sticky="e")
        for label, amount, unit in (
            ("1 MB", "1", DEFAULT_UNIT),
            ("10 MB", "10", DEFAULT_UNIT),
            ("100 MB", "100", DEFAULT_UNIT),
            ("1 GB", "1", "GB · Windows 1024³"),
        ):
            ctk.CTkButton(
                preset_frame,
                text=label,
                width=62,
                height=27,
                corner_radius=8,
                fg_color="transparent",
                border_width=1,
                border_color=BORDER,
                hover_color=("#EDF3FC", "#202C3E"),
                text_color=MUTED,
                font=self._font(10),
                command=lambda a=amount, u=unit: self._apply_preset(a, u),
            ).pack(side="left", padx=(5, 0))

        preview = ctk.CTkFrame(self.spec_card, corner_radius=11, fg_color=("#EEF4FF", "#14233E"))
        preview.grid(row=4, column=0, columnspan=3, padx=22, pady=(0, 20), sticky="ew")
        preview.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(preview, text="=", width=34, font=self._font(18, "bold"), text_color=ACCENT).grid(row=0, column=0, padx=(12, 0), pady=12)
        self.byte_preview = ctk.CTkLabel(preview, text="", font=self._font(13, "bold"), text_color=TEXT, anchor="w")
        self.byte_preview.grid(row=0, column=1, padx=(2, 12), pady=(10, 0), sticky="sw")
        self.windows_preview = ctk.CTkLabel(preview, text="", font=self._font(10), text_color=MUTED, anchor="w")
        self.windows_preview.grid(row=1, column=1, padx=(2, 12), pady=(0, 10), sticky="nw")

        self.options_card = self._card(3)
        self._section_title(self.options_card, "03", "生成选项", "安全写入并在完成后自动验证").grid(row=0, column=0, columnspan=2, padx=22, pady=(18, 10), sticky="ew")
        self.options_card.grid_columnconfigure(1, weight=1)
        self.overwrite_switch = ctk.CTkSwitch(
            self.options_card,
            text="允许覆盖同名文件",
            variable=self.overwrite_var,
            onvalue=True,
            offvalue=False,
            progress_color=ACCENT,
            fg_color=("#CBD5E1", "#334155"),
            border_color=("#94A3B8", "#52627A"),
            border_width=1,
            button_color=("#475569", "#E2E8F0"),
            button_hover_color=("#334155", "#FFFFFF"),
            text_color=TEXT,
            font=self._font(12),
        )
        self.overwrite_switch.grid(row=1, column=0, padx=22, pady=(5, 18), sticky="w")
        ctk.CTkLabel(
            self.options_card,
            text="✓ 原子写入    ✓ 精确尺寸校验    ✓ 格式结构验证    ✓ 大文件稀疏优化",
            font=self._font(10),
            text_color=("#348265", "#6ED1AA"),
        ).grid(row=1, column=1, padx=(10, 22), pady=(5, 18), sticky="e")

        # Keep the primary action visible even on smaller screens. The form
        # scrolls independently above it, like a modern settings application.
        self.action_card = ctk.CTkFrame(self, corner_radius=16, fg_color=CARD, border_width=1, border_color=BORDER)
        self.action_card.grid(row=1, column=1, padx=(34, 38), pady=(2, 22), sticky="ew")
        self.action_card.grid_columnconfigure(1, weight=1)

        self.status_dot = ctk.CTkLabel(self.action_card, text="●", width=26, font=self._font(18), text_color="#8AA0BB")
        self.status_dot.grid(row=0, column=0, rowspan=2, padx=(20, 8), pady=17)
        self.status_title = ctk.CTkLabel(self.action_card, text="准备就绪", font=self._font(13, "bold"), text_color=TEXT, anchor="w")
        self.status_title.grid(row=0, column=1, padx=0, pady=(15, 0), sticky="sw")
        self.status_detail = ctk.CTkLabel(self.action_card, text="填写上方信息后即可生成", font=self._font(10), text_color=MUTED, anchor="w")
        self.status_detail.grid(row=1, column=1, padx=0, pady=(0, 15), sticky="nw")

        self.open_button = ctk.CTkButton(
            self.action_card,
            text="打开文件",
            width=78,
            height=36,
            corner_radius=9,
            fg_color="transparent",
            border_width=1,
            border_color=BORDER,
            hover_color=CARD_ALT,
            text_color=TEXT,
            font=self._font(11),
            command=self._open_output,
        )
        self.folder_button = ctk.CTkButton(
            self.action_card,
            text="所在位置",
            width=82,
            height=36,
            corner_radius=9,
            fg_color="transparent",
            border_width=1,
            border_color=BORDER,
            hover_color=CARD_ALT,
            text_color=TEXT,
            font=self._font(11),
            command=self._open_folder,
        )

        self.generate_button = ctk.CTkButton(
            self.action_card,
            text="生成文件",
            width=126,
            height=42,
            corner_radius=11,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#FFFFFF",
            font=self._font(13, "bold"),
            command=self._start_create,
        )
        self.generate_button.grid(row=0, column=4, rowspan=2, padx=(12, 18), pady=14)

        self.progress = ctk.CTkProgressBar(self.action_card, mode="indeterminate", height=3, corner_radius=2, progress_color=ACCENT, fg_color=("#E7EDF5", "#253147"))
        self.progress.grid(row=2, column=0, columnspan=5, padx=18, pady=(0, 13), sticky="ew")
        self.progress.grid_remove()

    def _card(self, row: int) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.content, corner_radius=16, fg_color=CARD, border_width=1, border_color=BORDER)
        card.grid(row=row, column=0, padx=34, pady=(0, 14), sticky="ew")
        return card

    def _section_title(self, parent: ctk.CTkFrame, number: str, title: str, subtitle: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid_columnconfigure(1, weight=1)
        badge = ctk.CTkLabel(frame, text=number, width=34, height=27, corner_radius=8, fg_color=("#E8F0FF", "#182B50"), text_color=("#2864D7", "#7FACFF"), font=self._font(10, "bold"))
        badge.grid(row=0, column=0, rowspan=2, padx=(0, 10), sticky="w")
        ctk.CTkLabel(frame, text=title, font=self._font(14, "bold"), text_color=TEXT).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(frame, text=subtitle, font=self._font(10), text_color=MUTED).grid(row=1, column=1, sticky="w")
        return frame

    def _bind_updates(self) -> None:
        self.size_var.trace_add("write", lambda *_args: self._update_size_preview())
        self.output_var.trace_add("write", lambda *_args: self._sync_format_from_path())
        self.bind("<Return>", lambda _event: self._start_create())

    def _browse(self) -> None:
        spec = self._selected_spec()
        current = Path(self.output_var.get())
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="选择文件保存位置",
            initialdir=str(current.parent if current.parent.exists() else Path.home()),
            initialfile=current.name or f"blank.{spec.extensions[0]}",
            defaultextension=f".{spec.extensions[0]}",
            filetypes=[(f"{spec.name.upper()} 文件", "*." + spec.extensions[0]), ("所有文件", "*.*")],
        )
        if selected:
            self.output_var.set(selected)

    def _sync_format_from_path(self) -> None:
        extension = Path(self.output_var.get()).suffix.lower().lstrip(".")
        spec = fileforge.SPEC_BY_EXTENSION.get(extension)
        if spec and spec.name != self._selected_spec().name:
            self.format_var.set(self._label_for_format(spec.name))
            self._update_format_details()

    def _on_format_changed(self) -> None:
        spec = self._selected_spec()
        current = Path(self.output_var.get())
        if current.suffix.lower().lstrip(".") not in spec.extensions:
            self.output_var.set(str(current.with_suffix("." + spec.extensions[0])))
        self._update_format_details()
        self._update_size_preview()

    def _update_format_details(self) -> None:
        spec = self._selected_spec()
        color = CATEGORY_COLORS.get(spec.category, ACCENT)
        self.format_detail.configure(
            text=f"●  {spec.category}    ·    {', '.join('.' + ext for ext in spec.extensions)}    ·    最小 {spec.minimum:,} 字节",
            text_color=color,
        )

    def _size_bytes(self) -> int:
        token = UNIT_TOKENS[self.unit_var.get()]
        return fileforge.parse_size(f"{self.size_var.get()}{token}")

    def _update_size_preview(self) -> None:
        try:
            size = self._size_bytes()
            spec = self._selected_spec()
            if size < spec.minimum:
                self.byte_preview.configure(text=f"至少需要 {spec.minimum:,} 字节", text_color=DANGER)
                self.windows_preview.configure(text=f"当前值太小，无法构造有效的 {spec.name.upper()} 文件")
            else:
                self.byte_preview.configure(text=f"{size:,} 字节", text_color=TEXT)
                self.windows_preview.configure(text=f"Windows 资源管理器将显示约 {windows_size_label(size)}")
        except Exception:
            self.byte_preview.configure(text="请输入有效的正数", text_color=DANGER)
            self.windows_preview.configure(text="例如：10 MB 或 1.5 GB")

    def _apply_preset(self, amount: str, unit: str) -> None:
        self.size_var.set(amount)
        self.unit_var.set(unit)
        self._update_size_preview()

    def _change_theme(self, value: str) -> None:
        modes = {"跟随系统": "system", "浅色": "light", "深色": "dark"}
        ctk.set_appearance_mode(modes[value])

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.generate_button.configure(state="disabled" if busy else "normal", text="正在生成…" if busy else "生成文件")
        self.format_menu.configure(state="disabled" if busy else "readonly")
        self.unit_menu.configure(state="disabled" if busy else "normal")
        self.overwrite_switch.configure(state="disabled" if busy else "normal")
        if busy:
            self.open_button.grid_remove()
            self.folder_button.grid_remove()
            self.progress.grid()
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def _start_create(self) -> None:
        if self._busy:
            return
        try:
            output_text = self.output_var.get().strip()
            if not output_text:
                raise fileforge.FileForgeError("请选择输出文件")
            size = self._size_bytes()
            spec = self._selected_spec()
            if size < spec.minimum:
                raise fileforge.SizeTooSmallError(spec.name, size, spec.minimum)
            output = Path(output_text).expanduser().resolve()
            overwrite = bool(self.overwrite_var.get())
        except Exception as exc:
            self._show_error(str(exc))
            return

        self._set_busy(True)
        self.status_dot.configure(text_color=ACCENT)
        self.status_title.configure(text="正在创建文件", text_color=TEXT)
        self.status_detail.configure(text=f"{spec.name.upper()}  ·  {size:,} 字节  ·  正在写入并验证…")
        threading.Thread(target=self._worker, args=(output, size, spec.name, overwrite), daemon=True).start()
        self.after(100, self._poll_worker)

    def _worker(self, output: Path, size: int, fmt: str, overwrite: bool) -> None:
        try:
            created, spec = fileforge.create_file(output, size, fmt, overwrite)
            ok, detail = fileforge.verify_file(created, spec.name, deep=size <= 64 * 1024 * 1024)
            if not ok:
                raise fileforge.FileForgeError(detail)
            self._results.put(("ok", created, size))
        except Exception as exc:
            self._results.put(("error", exc, 0))

    def _poll_worker(self) -> None:
        try:
            status, value, size = self._results.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self.after(100, self._poll_worker)
            return
        self._set_busy(False)
        if status == "ok":
            self._last_output = Path(value)
            self.status_dot.configure(text="●", text_color=SUCCESS)
            self.status_title.configure(text="生成完成", text_color=SUCCESS)
            self.status_detail.configure(text=f"{self._last_output.name}  ·  {int(size):,} 字节  ·  已通过格式验证")
            self.open_button.grid(row=0, column=2, rowspan=2, padx=(10, 0), pady=14)
            self.folder_button.grid(row=0, column=3, rowspan=2, padx=(8, 0), pady=14)
        else:
            self._show_error(str(value))

    def _show_error(self, message: str) -> None:
        self.status_dot.configure(text="●", text_color=DANGER)
        self.status_title.configure(text="生成失败", text_color=DANGER)
        self.status_detail.configure(text=message)

    def _open_output(self) -> None:
        if self._last_output and self._last_output.exists():
            os.startfile(self._last_output)

    def _open_folder(self) -> None:
        if self._last_output and self._last_output.exists():
            os.startfile(self._last_output.parent)

    def _show_formats(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("FileForge 支持格式")
        dialog.geometry("760x610")
        dialog.minsize(660, 520)
        dialog.configure(fg_color=BG)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="支持格式", font=self._font(24, "bold"), text_color=TEXT).pack(anchor="w", padx=28, pady=(24, 2))
        ctk.CTkLabel(dialog, text="所有条目都会生成对应格式的最小合法结构。", font=self._font(11), text_color=MUTED).pack(anchor="w", padx=28, pady=(0, 16))
        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=22, pady=(0, 14))
        scroll.grid_columnconfigure(0, weight=1)

        grouped: dict[str, list[fileforge.FormatSpec]] = {}
        for spec in fileforge.SPECS:
            grouped.setdefault(spec.category, []).append(spec)
        row = 0
        for category, specs in grouped.items():
            ctk.CTkLabel(scroll, text=category, font=self._font(13, "bold"), text_color=CATEGORY_COLORS.get(category, ACCENT)).grid(row=row, column=0, padx=6, pady=(14 if row else 2, 7), sticky="w")
            row += 1
            for spec in specs:
                item = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10, border_width=1, border_color=BORDER)
                item.grid(row=row, column=0, padx=4, pady=3, sticky="ew")
                item.grid_columnconfigure(1, weight=1)
                ctk.CTkLabel(item, text=spec.name.upper(), width=76, font=self._font(11, "bold"), text_color=TEXT).grid(row=0, column=0, padx=(12, 8), pady=10, sticky="w")
                ctk.CTkLabel(item, text=spec.description, font=self._font(10), text_color=MUTED).grid(row=0, column=1, padx=8, pady=10, sticky="w")
                ctk.CTkLabel(item, text="  ".join("." + ext for ext in spec.extensions), font=self._font(10), text_color=MUTED).grid(row=0, column=2, padx=(8, 14), pady=10, sticky="e")
                row += 1
        ctk.CTkButton(dialog, text="关闭", width=100, height=38, corner_radius=10, command=dialog.destroy).pack(pady=(0, 20))

    def _on_close(self) -> None:
        self.destroy()


def launch_gui() -> None:
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = FileForgeApp()
    app.mainloop()


if __name__ == "__main__":
    launch_gui()
