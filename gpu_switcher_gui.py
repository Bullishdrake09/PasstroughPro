"""
PassthroughPro — PyQt6 GUI
A polished interface for switching ANY GPU between Host and QEMU/KVM VM.
"""

import sys
import os
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QFrame, QScrollArea,
    QProgressBar, QMessageBox, QSystemTrayIcon, QMenu, QSizePolicy,
    QGraphicsDropShadowEffect, QStackedWidget, QGridLayout, QGroupBox
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation,
    QEasingCurve, QSize, QRect, pyqtProperty, QPoint
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QPixmap, QPainter, QPainterPath,
    QLinearGradient, QRadialGradient, QIcon, QFontDatabase,
    QPen, QBrush, QPolygonF
)

try:
    from gpu_backend import (
        discover_gpu_devices, get_current_mode, get_grub_vfio_ids,
        is_device_grub_listed, switch_to_vm_mode, switch_to_host_mode,
        detect_running_vms, get_system_info, refresh_driver_state,
        GPUMode, PCIDevice, SwitchResult
    )
    BACKEND_AVAILABLE = True
except ImportError:
    BACKEND_AVAILABLE = False
    class GPUMode:
        HOST = "host"; VM = "vm"; UNKNOWN = "unknown"; TRANSITIONING = "transitioning"
    class PCIDevice:
        def __init__(self):
            self.address = "0000:01:00.0"; self.description = "Generic GPU"
            self.vendor_id = "xxxx"; self.device_id = "yyyy"
            self.current_driver = "host_driver"; self.is_grub_listed = True
        @property
        def id_pair(self): return f"{self.vendor_id}:{self.device_id}"
    def discover_gpu_devices(): return [PCIDevice()]
    def get_current_mode(devs): return GPUMode.HOST
    def get_grub_vfio_ids(): return ["xxxx:yyyy"]
    def is_device_grub_listed(dev, ids): return True
    def switch_to_vm_mode(devs, log): time.sleep(1); return type('R', (), {'success': True, 'mode': GPUMode.VM, 'messages': ['Mock VM switch'], 'warnings': [], 'errors': []})()
    def switch_to_host_mode(devs, log): time.sleep(1); return type('R', (), {'success': True, 'mode': GPUMode.HOST, 'messages': ['Mock Host switch'], 'warnings': [], 'errors': []})()
    def detect_running_vms(): return []
    def get_system_info(): return {"kernel": "6.8.0", "iommu_enabled": True, "vfio_loaded": False, "host_driver_loaded": True, "grub_vfio_ids": ["xxxx:yyyy"], "running_vms": []}
    def refresh_driver_state(devs): pass

# ══════════════════════════════════════════════════════
# DESIGN CONSTANTS
# ══════════════════════════════════════════════════════
DARK_BG       = "#0a0c10"
PANEL_BG      = "#0f1318"
CARD_BG       = "#141920"
BORDER_COLOR  = "#1e2530"
TEXT_PRIMARY  = "#e8edf5"
TEXT_SECONDARY = "#6b7a94"
TEXT_DIM      = "#3a4558"

HOST_GREEN    = "#00e676"
VFIO_BLUE     = "#0066ff"
ACCENT_CYAN   = "#00d4ff"
ERROR_RED     = "#ff3d5a"
WARN_AMBER    = "#ffb300"
SUCCESS_GREEN = "#00e676"

HOST_COLOR    = HOST_GREEN
VM_COLOR      = VFIO_BLUE


class SwitchWorker(QThread):
    log_signal   = pyqtSignal(str)
    done_signal  = pyqtSignal(bool, str, list, list)

    def __init__(self, target_mode: str, devices: list):
        super().__init__()
        self.target_mode = target_mode
        self.devices = devices

    def run(self):
        def log(msg):
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_signal.emit(f"[{ts}] {msg}")

        try:
            if self.target_mode == "vm":
                result = switch_to_vm_mode(self.devices, log)
            else:
                result = switch_to_host_mode(self.devices, log)

            mode_str = result.mode if isinstance(result.mode, str) else result.mode.value if hasattr(result.mode, 'value') else str(result.mode)
            self.done_signal.emit(result.success, mode_str, result.warnings, result.errors)
        except Exception as e:
            log(f"CRITICAL ERROR: {e}")
            self.done_signal.emit(False, "unknown", [], [str(e)])


class GlowLabel(QLabel):
    def __init__(self, text="", glow_color="#00d4ff", parent=None):
        super().__init__(text, parent)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(18)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(glow_color))
        self.setGraphicsEffect(self._glow)

    def set_glow_color(self, color: str):
        self._glow.setColor(QColor(color))


class ModeIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 120)
        self._mode = "unknown"
        self._pulse = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)
        self._phase = 0.0

    def set_mode(self, mode: str):
        self._mode = mode

    def _tick(self):
        self._phase = (self._phase + 0.08) % (2 * 3.14159)
        import math
        self._pulse = 0.5 + 0.5 * math.sin(self._phase)
        self.update()

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = 44

        if self._mode == "host":
            base_color = QColor(HOST_COLOR)
            icon_text = "H"
        elif self._mode == "vm":
            base_color = QColor(VM_COLOR)
            icon_text = "VM"
        elif self._mode == "transitioning":
            base_color = QColor(WARN_AMBER)
            icon_text = "…"
        else:
            base_color = QColor(TEXT_DIM)
            icon_text = "?"

        for i in range(4, 0, -1):
            glow = QColor(base_color)
            alpha = int(60 * self._pulse * (i / 4))
            glow.setAlpha(alpha)
            glow_pen = QPen(glow, i * 4)
            p.setPen(glow_pen)
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        track_color = QColor(BORDER_COLOR)
        p.setPen(QPen(track_color, 3))
        p.setBrush(QBrush(QColor(CARD_BG)))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        p.setPen(Qt.PenStyle.NoPen)
        grad = QRadialGradient(cx, cy - 10, r)
        bright = QColor(base_color)
        bright.setAlpha(220)
        dim = QColor(base_color)
        dim.setAlpha(100)
        grad.setColorAt(0, bright)
        grad.setColorAt(1, dim)
        p.setBrush(QBrush(grad))
        p.drawEllipse(cx - r + 4, cy - r + 4, (r - 4) * 2, (r - 4) * 2)

        p.setPen(QColor(TEXT_PRIMARY))
        font = QFont("JetBrains Mono", 14, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRect(cx - r, cy - r, r * 2, r * 2),
                   Qt.AlignmentFlag.AlignCenter, icon_text)
        p.end()


class StatusBadge(QLabel):
    def __init__(self, text="", color=ACCENT_CYAN, parent=None):
        super().__init__(text, parent)
        self._color = color
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_style()

    def set_status(self, text, color):
        self._color = color
        self.setText(text)
        self._update_style()

    def _update_style(self):
        self.setStyleSheet(f"""
            QLabel {{
                background: {self._color}22;
                color: {self._color};
                border: 1px solid {self._color}66;
                border-radius: 10px;
                padding: 3px 12px;
                font-family: 'JetBrains Mono', monospace;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 1px;
            }}
        """)


class AnimatedButton(QPushButton):
    def __init__(self, text="", accent_color=ACCENT_CYAN, parent=None):
        super().__init__(text, parent)
        self._accent = accent_color
        self._hovered = False
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style(False)

    def _update_style(self, hovered):
        bg = f"{self._accent}22" if not hovered else f"{self._accent}44"
        border = f"{self._accent}66" if not hovered else self._accent
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {self._accent};
                border: 1.5px solid {border};
                border-radius: 8px;
                font-family: 'JetBrains Mono', monospace;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 2px;
                padding: 0 24px;
            }}
            QPushButton:disabled {{
                background: {BORDER_COLOR}44;
                color: {TEXT_DIM};
                border-color: {BORDER_COLOR};
            }}
        """)

    def enterEvent(self, e):
        self._hovered = True
        self._update_style(True)

    def leaveEvent(self, e):
        self._hovered = False
        self._update_style(False)


class DeviceCard(QFrame):
    def __init__(self, device: PCIDevice, grub_ids: list, parent=None):
        super().__init__(parent)
        self.device = device
        self.setStyleSheet(f"""
            QFrame {{
                background: {CARD_BG};
                border: 1px solid {BORDER_COLOR};
                border-radius: 10px;
                padding: 4px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        addr_label = QLabel(device.address)
        addr_label.setStyleSheet(f"color: {ACCENT_CYAN}; font-family: 'JetBrains Mono'; font-size: 11px; font-weight: 700;")
        top.addWidget(addr_label)
        top.addStretch()

        in_grub = is_device_grub_listed(device, grub_ids)
        badge = StatusBadge(
            "GRUB ✓" if in_grub else "NOT IN GRUB",
            SUCCESS_GREEN if in_grub else WARN_AMBER
        )
        top.addWidget(badge)
        layout.addLayout(top)

        desc = QLabel(device.description)
        desc.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        bot = QHBoxLayout()
        id_label = QLabel(f"ID: {device.id_pair}")
        id_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-family: 'JetBrains Mono'; font-size: 10px;")
        bot.addWidget(id_label)
        bot.addStretch()

        drv = device.current_driver or "none"
        drv_color = VM_COLOR if drv == "vfio-pci" else (HOST_COLOR if drv != "none" else TEXT_DIM)
        drv_label = StatusBadge(drv, drv_color)
        bot.addWidget(drv_label)
        layout.addLayout(bot)

        if not in_grub:
            warn = QLabel("⚠  Not listed in GRUB vfio-pci.ids — switching may not persist across reboots")
            warn.setStyleSheet(f"color: {WARN_AMBER}; font-size: 10px; margin-top: 4px;")
            warn.setWordWrap(True)
            layout.addWidget(warn)


class LogView(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setStyleSheet(f"""
            QTextEdit {{
                background: #080b0f;
                color: #8ab4d4;
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
                font-family: 'JetBrains Mono', 'Courier New', monospace;
                font-size: 11px;
                padding: 12px;
                selection-background-color: {VFIO_BLUE}44;
            }}
            QScrollBar:vertical {{
                background: {PANEL_BG};
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER_COLOR};
                border-radius: 3px;
            }}
        """)

    def append_log(self, msg: str):
        if "ERROR" in msg or "✗" in msg or "CRITICAL" in msg:
            color = ERROR_RED
        elif "WARNING" in msg or "⚠" in msg:
            color = WARN_AMBER
        elif "✓" in msg or "complete" in msg.lower() or "success" in msg.lower():
            color = SUCCESS_GREEN
        elif "═══" in msg:
            color = ACCENT_CYAN
        elif "Step" in msg:
            color = "#a78bfa"
        else:
            color = "#8ab4d4"
        self.append(f'<span style="color:{color};">{msg}</span>')
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class GPUSwitcherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PassthroughPro")
        self.setMinimumSize(920, 720)
        self.resize(1080, 800)
        self._devices = []
        self._grub_ids = []
        self._current_mode = GPUMode.UNKNOWN
        self._worker = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(8000)

        self._setup_fonts()
        self._setup_theme()
        self._build_ui()
        self._refresh_state()

    def _setup_fonts(self):
        QFontDatabase.addApplicationFont("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")

    def _setup_theme(self):
        app = QApplication.instance()
        app.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(DARK_BG))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRIMARY))
        palette.setColor(QPalette.ColorRole.Base, QColor(PANEL_BG))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(CARD_BG))
        palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_PRIMARY))
        palette.setColor(QPalette.ColorRole.Button, QColor(CARD_BG))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(VFIO_BLUE))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT_PRIMARY))
        app.setPalette(palette)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background: {DARK_BG};")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: {DARK_BG}; }}
            QScrollBar:vertical {{ background: {DARK_BG}; width: 6px; }}
            QScrollBar::handle:vertical {{ background: {BORDER_COLOR}; border-radius: 3px; }}
        """)
        content_widget = QWidget()
        content_widget.setStyleSheet(f"background: {DARK_BG};")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(28, 24, 28, 28)
        content_layout.setSpacing(20)
        content_scroll.setWidget(content_widget)
        root.addWidget(content_scroll)

        row1 = QHBoxLayout()
        row1.setSpacing(20)
        row1.addWidget(self._build_mode_panel(), stretch=2)
        row1.addWidget(self._build_system_panel(), stretch=1)
        content_layout.addLayout(row1)

        self._devices_container = QVBoxLayout()
        self._devices_group = self._build_labeled_group("PCI DEVICES", self._devices_container)
        content_layout.addWidget(self._devices_group)

        content_layout.addWidget(self._build_actions_panel())
        content_layout.addWidget(self._build_log_panel())

    def _build_header(self) -> QWidget:
        hdr = QWidget()
        hdr.setFixedHeight(64)
        hdr.setStyleSheet(f"""
            QWidget {{
                background: {PANEL_BG};
                border-bottom: 1px solid {BORDER_COLOR};
            }}
        """)
        layout = QHBoxLayout(hdr)
        layout.setContentsMargins(28, 0, 28, 0)

        title = QLabel("PASSTHROUGHPRO")
        title.setStyleSheet(f"""
            color: {TEXT_PRIMARY};
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            font-weight: 800;
            letter-spacing: 4px;
        """)
        layout.addWidget(title)
        layout.addStretch()

        ver = StatusBadge("v1.0 Universal GPU", ACCENT_CYAN)
        layout.addWidget(ver)
        layout.addSpacing(16)

        if os.geteuid() != 0:
            warn = StatusBadge("⚠ NOT ROOT — LIMITED FUNCTIONALITY", WARN_AMBER)
            layout.addWidget(warn)
            layout.addSpacing(16)

        refresh_btn = QPushButton("↻  REFRESH")
        refresh_btn.setFixedSize(110, 34)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER_COLOR};
                border-radius: 6px;
                font-family: 'JetBrains Mono', monospace;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                color: {ACCENT_CYAN};
                border-color: {ACCENT_CYAN}66;
            }}
        """)
        refresh_btn.clicked.connect(self._refresh_state)
        layout.addWidget(refresh_btn)
        return hdr

    def _build_mode_panel(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {PANEL_BG};
                border: 1px solid {BORDER_COLOR};
                border-radius: 14px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        top = QHBoxLayout()
        self._mode_indicator = ModeIndicator()
        top.addWidget(self._mode_indicator)
        top.addSpacing(24)

        text_col = QVBoxLayout()
        text_col.setSpacing(6)

        mode_caption = QLabel("CURRENT MODE")
        mode_caption.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px; font-family: 'JetBrains Mono', monospace; letter-spacing: 3px;")
        text_col.addWidget(mode_caption)

        self._mode_label = GlowLabel("DETECTING…", ACCENT_CYAN)
        self._mode_label.setStyleSheet(f"""
            color: {ACCENT_CYAN};
            font-family: 'JetBrains Mono', monospace;
            font-size: 26px;
            font-weight: 800;
            letter-spacing: 3px;
        """)
        text_col.addWidget(self._mode_label)

        self._mode_desc = QLabel("Scanning system state…")
        self._mode_desc.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        self._mode_desc.setWordWrap(True)
        text_col.addWidget(self._mode_desc)

        top.addLayout(text_col)
        top.addStretch()
        layout.addLayout(top)

        self._vm_status = QLabel("")
        self._vm_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 6px 0;")
        self._vm_status.setWordWrap(True)
        layout.addWidget(self._vm_status)

        return frame

    def _build_system_panel(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {PANEL_BG};
                border: 1px solid {BORDER_COLOR};
                border-radius: 14px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        caption = QLabel("SYSTEM STATUS")
        caption.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px; font-family: 'JetBrains Mono', monospace; letter-spacing: 3px;")
        layout.addWidget(caption)

        self._sys_grid = QGridLayout()
        self._sys_grid.setSpacing(8)
        self._sys_grid.setColumnStretch(1, 1)
        layout.addLayout(self._sys_grid)
        layout.addStretch()

        self._sys_rows = {}
        for key in ["Kernel", "IOMMU", "VFIO Mod", "Host Driver", "GRUB IDs", "Running VMs"]:
            row = self._sys_grid.rowCount()
            lbl = QLabel(key)
            lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px; min-width: 80px;")
            val = QLabel("—")
            val.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 10px; font-family: 'JetBrains Mono', monospace;")
            val.setWordWrap(True)
            self._sys_grid.addWidget(lbl, row, 0)
            self._sys_grid.addWidget(val, row, 1)
            self._sys_rows[key] = val

        return frame

    def _build_labeled_group(self, label: str, inner_layout: QVBoxLayout) -> QWidget:
        wrapper = QWidget()
        vbox = QVBoxLayout(wrapper)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        cap = QLabel(label)
        cap.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px; font-family: 'JetBrains Mono', monospace; letter-spacing: 3px;")
        vbox.addWidget(cap)

        inner_widget = QWidget()
        inner_widget.setLayout(inner_layout)
        vbox.addWidget(inner_widget)
        return wrapper

    def _build_actions_panel(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {PANEL_BG};
                border: 1px solid {BORDER_COLOR};
                border-radius: 14px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        cap = QLabel("ACTIONS")
        cap.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px; font-family: 'JetBrains Mono', monospace; letter-spacing: 3px;")
        layout.addWidget(cap)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        self._host_btn = AnimatedButton("⬡  SWITCH TO HOST MODE", HOST_COLOR)
        self._host_btn.setToolTip("Unbind from VFIO → load host driver → GPU available to host OS")
        self._host_btn.clicked.connect(self._on_switch_host)
        btn_row.addWidget(self._host_btn)

        self._vm_btn = AnimatedButton("⬢  SWITCH TO VM MODE", VM_COLOR)
        self._vm_btn.setToolTip("Unload host driver → bind to vfio-pci → GPU available to QEMU/KVM")
        self._vm_btn.clicked.connect(self._on_switch_vm)
        btn_row.addWidget(self._vm_btn)

        layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background: {BORDER_COLOR};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {VFIO_BLUE}, stop:1 {ACCENT_CYAN});
                border-radius: 2px;
            }}
        """)
        layout.addWidget(self._progress)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        layout.addWidget(self._status_label)

        return frame

    def _build_log_panel(self) -> QWidget:
        wrapper = QWidget()
        vbox = QVBoxLayout(wrapper)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        header = QHBoxLayout()
        cap = QLabel("OPERATION LOG")
        cap.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px; font-family: 'JetBrains Mono', monospace; letter-spacing: 3px;")
        header.addWidget(cap)
        header.addStretch()

        clear_btn = QPushButton("CLEAR")
        clear_btn.setFixedSize(64, 24)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {TEXT_DIM};
                border: 1px solid {BORDER_COLOR};
                border-radius: 4px;
                font-family: 'JetBrains Mono', monospace;
                font-size: 9px;
            }}
            QPushButton:hover {{ color: {ERROR_RED}; border-color: {ERROR_RED}66; }}
        """)
        clear_btn.clicked.connect(lambda: self._log.clear())
        header.addWidget(clear_btn)
        vbox.addLayout(header)

        self._log = LogView()
        self._log.setMinimumHeight(200)
        vbox.addWidget(self._log)

        return wrapper

    def _refresh_state(self):
        self._log.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] Refreshing system state…")
        try:
            self._grub_ids = get_grub_vfio_ids()
            self._devices = discover_gpu_devices()
            for d in self._devices:
                d.is_grub_listed = is_device_grub_listed(d, self._grub_ids)

            self._current_mode = get_current_mode(self._devices)
            mode_str = self._current_mode if isinstance(self._current_mode, str) else self._current_mode.value if hasattr(self._current_mode, 'value') else str(self._current_mode)
            self._update_mode_display(mode_str)
            self._update_device_cards()
            self._update_system_info()
            self._update_button_states(mode_str)
        except Exception as e:
            self._log.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR refreshing state: {e}")

    def _update_mode_display(self, mode: str):
        self._mode_indicator.set_mode(mode)
        if mode == "host":
            self._mode_label.setText("HOST MODE")
            self._mode_label.setStyleSheet(f"color: {HOST_COLOR}; font-family: 'JetBrains Mono'; font-size: 26px; font-weight: 800; letter-spacing: 3px;")
            self._mode_label.set_glow_color(HOST_COLOR)
            self._mode_desc.setText("GPU is bound to the host driver. Host OS has full access.")
        elif mode == "vm":
            self._mode_label.setText("VM MODE")
            self._mode_label.setStyleSheet(f"color: {VM_COLOR}; font-family: 'JetBrains Mono'; font-size: 26px; font-weight: 800; letter-spacing: 3px;")
            self._mode_label.set_glow_color(VM_COLOR)
            self._mode_desc.setText("GPU is bound to vfio-pci. Ready for QEMU/KVM passthrough.")
        elif mode == "transitioning":
            self._mode_label.setText("SWITCHING…")
            self._mode_label.setStyleSheet(f"color: {WARN_AMBER}; font-family: 'JetBrains Mono'; font-size: 26px; font-weight: 800; letter-spacing: 3px;")
            self._mode_label.set_glow_color(WARN_AMBER)
            self._mode_desc.setText("Driver switch in progress. Please wait…")
        else:
            self._mode_label.setText("UNKNOWN")
            self._mode_label.setStyleSheet(f"color: {TEXT_DIM}; font-family: 'JetBrains Mono'; font-size: 26px; font-weight: 800; letter-spacing: 3px;")
            self._mode_label.set_glow_color(TEXT_DIM)
            self._mode_desc.setText("Could not determine GPU state. Check if GPU devices are present.")

        vms = detect_running_vms()
        if vms:
            self._vm_status.setText(f"🟢  Running VMs: {', '.join(vms)}")
            self._vm_status.setStyleSheet(f"color: {SUCCESS_GREEN}; font-size: 11px;")
        else:
            self._vm_status.setText("○  No VMs currently running")
            self._vm_status.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")

    def _update_device_cards(self):
        while self._devices_container.count():
            item = self._devices_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._devices:
            placeholder = QLabel("No GPU devices detected. Ensure the GPU is installed and lspci is available.")
            placeholder.setStyleSheet(f"color: {WARN_AMBER}; font-size: 12px; padding: 12px;")
            self._devices_container.addWidget(placeholder)
            return

        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        for dev in self._devices:
            card = DeviceCard(dev, self._grub_ids)
            cards_row.addWidget(card)
        cards_row.addStretch()
        cards_widget = QWidget()
        cards_widget.setLayout(cards_row)
        self._devices_container.addWidget(cards_widget)

    def _update_system_info(self):
        info = get_system_info()
        self._sys_rows["Kernel"].setText(info.get("kernel", "—"))

        iommu = info.get("iommu_enabled", False)
        self._sys_rows["IOMMU"].setText("Enabled ✓" if iommu else "⚠ Disabled")
        self._sys_rows["IOMMU"].setStyleSheet(f"color: {SUCCESS_GREEN if iommu else WARN_AMBER}; font-size: 10px; font-family: 'JetBrains Mono', monospace;")

        vfio = info.get("vfio_loaded", False)
        self._sys_rows["VFIO Mod"].setText("Loaded ✓" if vfio else "Not loaded")
        self._sys_rows["VFIO Mod"].setStyleSheet(f"color: {SUCCESS_GREEN if vfio else TEXT_DIM}; font-size: 10px; font-family: 'JetBrains Mono', monospace;")

        nv = info.get("host_driver_loaded", False)
        self._sys_rows["Host Driver"].setText("Loaded ✓" if nv else "Not loaded")
        self._sys_rows["Host Driver"].setStyleSheet(f"color: {SUCCESS_GREEN if nv else TEXT_DIM}; font-size: 10px; font-family: 'JetBrains Mono', monospace;")

        grub_ids = info.get("grub_vfio_ids", [])
        self._sys_rows["GRUB IDs"].setText(", ".join(grub_ids) if grub_ids else "None found")

        vms = info.get("running_vms", [])
        self._sys_rows["Running VMs"].setText(", ".join(vms) if vms else "None")

    def _update_button_states(self, mode: str):
        self._host_btn.setEnabled(mode != "host" and mode != "transitioning")
        self._vm_btn.setEnabled(mode != "vm" and mode != "transitioning")

    def _on_switch_host(self):
        vms = detect_running_vms()
        if vms:
            QMessageBox.critical(self, "VMs Running",
                f"Cannot switch to host mode while VMs are running:\n\n{chr(10).join(vms)}\n\nShut down all VMs first.")
            return
        self._confirm_and_switch("host")

    def _on_switch_vm(self):
        mode_str = self._current_mode if isinstance(self._current_mode, str) else self._current_mode.value if hasattr(self._current_mode, 'value') else str(self._current_mode)
        if mode_str == "host":
            try:
                r = subprocess.run(["pgrep", "-x", "Xorg"], capture_output=True)
                if r.returncode == 0:
                    reply = QMessageBox.warning(self, "Xorg Running",
                        "Xorg is currently running on this GPU.\n\nSwitching to VM mode will likely crash your display session.\n\nProceed anyway?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if reply != QMessageBox.StandardButton.Yes:
                        return
            except Exception:
                pass
        self._confirm_and_switch("vm")

    def _confirm_and_switch(self, target: str):
        unlisted = [d for d in self._devices if not d.is_grub_listed]
        grub_warn = ""
        if unlisted and target == "vm":
            grub_warn = f"\n\n⚠ {len(unlisted)} device(s) not found in GRUB vfio-pci.ids.\nThe switch will work for this session but may not persist after reboot."

        reply = QMessageBox.question(self, f"Switch to {'HOST' if target == 'host' else 'VM'} Mode",
            f"Switch GPU to {'HOST' if target == 'host' else 'VM (vfio-pci)'}?\n\nThis will rebind PCI devices. Ensure no display or VM is actively using the GPU.{grub_warn}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._start_switch(target)

    def _start_switch(self, target: str):
        self._host_btn.setEnabled(False)
        self._vm_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._update_mode_display("transitioning")
        self._status_label.setText(f"Switching to {'host' if target == 'host' else 'VM'} mode…")
        self._log.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] ──────────────────────────────────────")

        self._worker = SwitchWorker(target, self._devices)
        self._worker.log_signal.connect(self._log.append_log)
        self._worker.done_signal.connect(self._on_switch_done)
        self._worker.start()

    def _on_switch_done(self, success: bool, mode: str, warnings: list, errors: list):
        self._progress.setVisible(False)

        if success:
            self._status_label.setText(f"✓ Switch to {mode.upper()} mode successful")
            self._status_label.setStyleSheet(f"color: {SUCCESS_GREEN}; font-size: 11px;")
        else:
            self._status_label.setText(f"✗ Switch failed — see log for details")
            self._status_label.setStyleSheet(f"color: {ERROR_RED}; font-size: 11px;")

        if warnings:
            for w in warnings:
                self._log.append_log(f"[⚠ WARNING] {w}")
        if errors:
            for e in errors:
                self._log.append_log(f"[✗ ERROR] {e}")

        QTimer.singleShot(800, self._refresh_state)

    def _auto_refresh(self):
        if self._worker and self._worker.isRunning():
            return
        try:
            refresh_driver_state(self._devices)
            new_mode = get_current_mode(self._devices)
            mode_str = new_mode if isinstance(new_mode, str) else new_mode.value if hasattr(new_mode, 'value') else str(new_mode)
            old_str = self._current_mode if isinstance(self._current_mode, str) else self._current_mode.value if hasattr(self._current_mode, 'value') else str(self._current_mode)
            if mode_str != old_str:
                self._current_mode = new_mode
                self._update_mode_display(mode_str)
                self._update_button_states(mode_str)
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PassthroughPro")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("vfio-tools")

    window = GPUSwitcherWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()