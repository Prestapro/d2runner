from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
import sys

from .controller import ControllerBackend, ControllerConfig, load_controller_config, save_controller_config
from .core import CsvRunLogger, RunTracker
from .hotkeys import ACTION_ORDER, ACTION_TITLES, HotkeyBackend, human_combo_label, normalize_combo_string


def _qt_imports():
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QGraphicsDropShadowEffect,
        QGridLayout,
        QHBoxLayout,
        QKeySequenceEdit,
        QLabel,
        QLineEdit,
        QMessageBox,
        QMenu,
        QPushButton,
        QSpinBox,
        QStyle,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )

    return {
        "QAction": QAction,
        "QApplication": QApplication,
        "QCheckBox": QCheckBox,
        "QColor": QColor,
        "QComboBox": QComboBox,
        "QDialog": QDialog,
        "QDialogButtonBox": QDialogButtonBox,
        "QFrame": QFrame,
        "QGraphicsDropShadowEffect": QGraphicsDropShadowEffect,
        "QGridLayout": QGridLayout,
        "QHBoxLayout": QHBoxLayout,
        "QKeySequence": QKeySequence,
        "QKeySequenceEdit": QKeySequenceEdit,
        "QLabel": QLabel,
        "QLineEdit": QLineEdit,
        "QMessageBox": QMessageBox,
        "QMenu": QMenu,
        "QPushButton": QPushButton,
        "QShortcut": QShortcut,
        "QSpinBox": QSpinBox,
        "QStyle": QStyle,
        "QSystemTrayIcon": QSystemTrayIcon,
        "QTimer": QTimer,
        "Qt": Qt,
        "QVBoxLayout": QVBoxLayout,
        "QWidget": QWidget,
    }


def _internal_combo_to_qt_portable(combo: str) -> str:
    combo = normalize_combo_string(combo)
    if not combo:
        return ""
    parts = combo.split("+")
    out: list[str] = []
    for p in parts:
        out.append(
            {
                "cmd": "Meta",
                "ctrl": "Ctrl",
                "alt": "Alt",
                "shift": "Shift",
                "enter": "Return",
                "esc": "Esc",
                "space": "Space",
                "tab": "Tab",
            }.get(p, p.upper() if len(p) == 1 else p.title())
        )
    return "+".join(out)


def _qt_portable_to_internal(portable: str) -> str:
    if not portable:
        return ""
    parts = [p.strip() for p in portable.split("+") if p.strip()]
    mapped: list[str] = []
    for p in parts:
        pl = p.lower()
        mapped.append(
            {
                "meta": "cmd",
                "ctrl": "ctrl",
                "alt": "alt",
                "shift": "shift",
                "return": "enter",
                "esc": "esc",
                "space": "space",
                "tab": "tab",
            }.get(pl, pl)
        )
    return normalize_combo_string("+".join(mapped))


class D2RunnerQtApp:
    SESSION_RUN_LIMIT = 500

    def __init__(self, csv_path: Path, controller_config_path: Path, overlay_mode: str = "off") -> None:
        self.qt = _qt_imports()
        self.QTimer = self.qt["QTimer"]
        self.Qt = self.qt["Qt"]
        self.QApplication = self.qt["QApplication"]
        self.QWidget = self.qt["QWidget"]
        self.QAction = self.qt["QAction"]
        self.QVBoxLayout = self.qt["QVBoxLayout"]
        self.QHBoxLayout = self.qt["QHBoxLayout"]
        self.QGridLayout = self.qt["QGridLayout"]
        self.QFrame = self.qt["QFrame"]
        self.QGraphicsDropShadowEffect = self.qt["QGraphicsDropShadowEffect"]
        self.QLabel = self.qt["QLabel"]
        self.QLineEdit = self.qt["QLineEdit"]
        self.QPushButton = self.qt["QPushButton"]
        self.QShortcut = self.qt["QShortcut"]
        self.QKeySequence = self.qt["QKeySequence"]
        self.QDialog = self.qt["QDialog"]
        self.QDialogButtonBox = self.qt["QDialogButtonBox"]
        self.QComboBox = self.qt["QComboBox"]
        self.QCheckBox = self.qt["QCheckBox"]
        self.QColor = self.qt["QColor"]
        self.QMenu = self.qt["QMenu"]
        self.QSpinBox = self.qt["QSpinBox"]
        self.QStyle = self.qt["QStyle"]
        self.QSystemTrayIcon = self.qt["QSystemTrayIcon"]
        self.QKeySequenceEdit = self.qt["QKeySequenceEdit"]
        self.QMessageBox = self.qt["QMessageBox"]

        self.log = logging.getLogger("d2runner")
        self.overlay_mode = overlay_mode
        self.command_queue: Queue[tuple[str, str]] = Queue()
        self.base_csv_path = Path(csv_path)
        self.current_csv_path = self._new_session_csv_path()
        self.tracker = RunTracker(CsvRunLogger(self.current_csv_path))
        self.controller_config_path = Path(controller_config_path)
        self.controller_config = load_controller_config(self.controller_config_path, self.log)
        self.hotkeys = HotkeyBackend(
            lambda action: self.command_queue.put(("global_hotkey", action)),
            self.controller_config.keyboard_map,
        )
        self.controller = ControllerBackend(
            lambda action: self.command_queue.put(("controller", action)),
            self.controller_config,
        )

        self.qt_app = self.QApplication.instance() or self.QApplication(sys.argv)
        self.window = self.QWidget()
        self.window.setWindowTitle("D2 Runner")
        self.window.setWindowFlag(self.Qt.WindowStaysOnTopHint, True)
        self.window.setMinimumWidth(560)
        self.window.setObjectName("MainWindow")
        if self.overlay_mode in {"compact", "mini"}:
            self.window.setMinimumWidth(320)
            try:
                self.window.setWindowOpacity(0.5 if self.overlay_mode == "compact" else 0.94)
                self.window.setWindowFlag(self.Qt.Tool, True)
            except Exception:
                pass

        self._qt_shortcuts: list[object] = []
        self._tray = None
        self._tray_enabled = False
        self._quitting = False
        self._default_min_width = 560
        self._build_theme()
        self._build_ui()
        self._setup_tray()
        self._apply_overlay_mode()
        self._rebuild_qt_shortcuts()
        self._update_labels()
        self._update_control_states()
        self._refresh_visual_state()

        self.ui_timer = self.QTimer(self.window)
        self.ui_timer.timeout.connect(self._tick)  # type: ignore[attr-defined]
        self.ui_timer.start(100)

    def _apply_overlay_mode(self) -> None:
        # Reset visibility first, then apply mode-specific hiding.
        self.window.setMinimumWidth(self._default_min_width)
        try:
            self.window.setMinimumSize(0, 0)
            self.window.setMaximumSize(16777215, 16777215)
            self.window.setWindowOpacity(1.0)
        except Exception:
            pass
        try:
            self.card_layout.setContentsMargins(16, 16, 16, 16)
            self.card_layout.setSpacing(12)
        except Exception:
            pass
        self.run_label.setStyleSheet("font-weight: 700; font-size: 16px;")
        self.timer_label.setStyleSheet("")
        for w in [
            self.session_card,
            self.timer_card,
            self.compact_panel,
            self.run_label,
            self.session_label,
            self.csv_label,
            self.limit_label,
            self.timer_label,
            self.state_chip,
            self.note_label,
            self.note_entry,
            self.btn_start_stop,
            self.btn_next_run,
            self.btn_reset_timer,
            self.btn_reset_session,
            self.btn_undo,
            self.btn_settings,
            self.btn_hide_tray,
            self.btn_mini_mode,
            self.status_label,
            self.help_label,
        ]:
            try:
                w.show()
            except Exception:
                pass

        if self.overlay_mode not in {"compact", "mini"}:
            try:
                self.window.resize(max(self.window.width(), 560), max(self.window.height(), 360))
            except Exception:
                pass
            return

        self.log.info("qt_overlay_mode_enabled mode=%s", self.overlay_mode)
        try:
            if self.overlay_mode == "mini":
                self.window.setMinimumWidth(255)
                self.window.setWindowOpacity(0.75)
                self.window.setFixedSize(255, 105)
            else:
                # Compact mode: fixed tiny overlay (run number + timer only).
                self.window.setMinimumWidth(100)
                self.window.setWindowOpacity(0.5)
                self.window.setFixedSize(100, 50)
        except Exception:
            pass

        for w in [self.session_label, self.csv_label, self.help_label]:
            try:
                w.hide()
            except Exception:
                pass

        if self.overlay_mode in {"compact", "mini"}:
            # Tiny overlay modes: dedicated compact panel to guarantee exact size.
            try:
                self.card_layout.setContentsMargins(1, 1, 1, 1)
                self.card_layout.setSpacing(0)
            except Exception:
                pass
            if self.overlay_mode == "compact":
                self.compact_run_label.setStyleSheet("font-weight: 700; font-size: 7px;")
                self.compact_timer_label.setStyleSheet("font-size: 11px; font-weight: 700;")
                try:
                    self.compact_layout.setContentsMargins(3, 2, 3, 2)
                    self.compact_layout.setSpacing(0)
                except Exception:
                    pass
            else:
                self.compact_run_label.setStyleSheet("font-weight: 700; font-size: 15px;")
                self.compact_timer_label.setStyleSheet("font-size: 26px; font-weight: 700;")
                try:
                    self.compact_layout.setContentsMargins(9, 6, 9, 6)
                    self.compact_layout.setSpacing(2)
                except Exception:
                    pass
            for w in [
                self.session_card,
                self.timer_card,
                self.session_label,
                self.csv_label,
                self.limit_label,
                self.state_chip,
                self.note_label,
                self.note_entry,
                self.btn_start_stop,
                self.btn_next_run,
                self.btn_reset_timer,
                self.btn_reset_session,
                self.btn_undo,
                self.btn_settings,
                self.btn_hide_tray,
                self.btn_mini_mode,
                self.status_label,
                self.help_label,
            ]:
                try:
                    w.hide()
                except Exception:
                    pass
            try:
                self.compact_panel.show()
            except Exception:
                pass
        else:
            # Legacy overlay branch (currently unused for compact/mini).
            for w in [
                self.compact_panel,
                self.limit_label,
                self.state_chip,
                self.note_label,
                self.note_entry,
                self.btn_start_stop,
                self.btn_next_run,
                self.btn_reset_timer,
                self.btn_reset_session,
                self.btn_undo,
                self.btn_settings,
                self.btn_hide_tray,
                self.btn_mini_mode,
                self.status_label,
                self.help_label,
            ]:
                try:
                    w.hide()
                except Exception:
                    pass

        # Compact keeps primary actions visible; hide less-used controls to reduce obstruction.
        if self.overlay_mode == "compact":
            pass
        else:
            for w in []:
                try:
                    w.hide()
                except Exception:
                    pass

    def _set_overlay_mode(self, mode: str) -> None:
        mode = mode if mode in {"off", "compact", "mini"} else "off"
        self.overlay_mode = mode
        self._apply_overlay_mode()
        self._update_labels()
        self._update_control_states()
        self._refresh_visual_state()
        if self.window.isVisible():
            self._position_overlay()
        self.log.info("qt_overlay_mode_switched mode=%s", mode)

    def _toggle_mini_mode(self) -> None:
        self._set_overlay_mode("off" if self.overlay_mode == "mini" else "mini")

    def _position_overlay(self) -> None:
        if self.overlay_mode not in {"compact", "mini"}:
            return
        try:
            screen = self.window.screen() or self.qt_app.primaryScreen()
            if screen is None:
                return
            geom = screen.availableGeometry()
            margin = 18
            self.window.adjustSize()
            x = geom.x() + geom.width() - self.window.width() - margin
            y = geom.y() + margin
            self.window.move(x, y)
        except Exception:
            pass

    def _build_theme(self) -> None:
        if sys.platform == "darwin":
            self.window.setStyleSheet(
                """
                /* ── Page / Window background ── */
                QWidget#MainWindow {
                    font-family: Inter, -apple-system, "system-ui", "SF Pro Text", "Helvetica Neue", sans-serif;
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0   #0b1120,
                        stop:0.3 #162040,
                        stop:0.5 #1a2848,
                        stop:0.7 #2d5a5e,
                        stop:0.85 #7c5a5e,
                        stop:1   #0f1a30
                    );
                    color: #1d1d1f;
                }

                /* ── Main glass card (window chrome) ── */
                QFrame#Card {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0   rgba(255, 255, 255, 210),
                        stop:0.5 rgba(255, 255, 255, 185),
                        stop:1   rgba(255, 255, 255, 195)
                    );
                    border: 1px solid rgba(255, 255, 255, 200);
                    border-radius: 0px;
                }

                /* ── Session info card ── */
                QFrame#SessionCard {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(255, 255, 255, 120),
                        stop:1 rgba(255, 255, 255, 50)
                    );
                    border: none;
                    border-radius: 14px;
                }

                /* ── Timer card ── */
                QFrame#TimerCard {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(255, 255, 255, 150),
                        stop:1 rgba(255, 255, 255, 60)
                    );
                    border: none;
                    border-radius: 14px;
                }

                /* ── Meta labels ── */
                QLabel#Meta {
                    font-family: "SF Mono", Menlo, Monaco, "Courier New", monospace;
                    color: rgba(29, 29, 31, 128);
                    font-size: 12px;
                    font-weight: 400;
                }

                /* ── Help / footer text ── */
                QLabel#Help {
                    font-family: Inter, -apple-system, "system-ui", "SF Pro Text", "Helvetica Neue", sans-serif;
                    color: rgba(29, 29, 31, 102);
                    font-size: 11px;
                    font-weight: 400;
                }

                /* ── Timer display ── */
                QLabel#Timer {
                    font-family: "SF Mono", Menlo, Monaco, "Courier New", monospace;
                    color: #1d1d1f;
                    font-size: 48px;
                    font-weight: 700;
                    letter-spacing: -1.2px;
                }

                /* ── State chip (Idle / Running / Paused) ── */
                QLabel#StateChip {
                    font-family: Inter, -apple-system, "system-ui", "SF Pro Text", "Helvetica Neue", sans-serif;
                    border-radius: 10px;
                    padding: 4px 12px;
                    font-weight: 600;
                    font-size: 12px;
                    color: #86868b;
                    min-height: 18px;
                    max-width: 120px;
                }

                /* ── Status bar ── */
                QLabel#Status {
                    font-family: Inter, -apple-system, "system-ui", "SF Pro Text", "Helvetica Neue", sans-serif;
                    border: 1px solid rgba(255, 255, 255, 80);
                    border-radius: 10px;
                    padding: 8px 12px;
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(255, 255, 255, 153),
                        stop:1 rgba(255, 255, 255, 77)
                    );
                    color: #1d1d1f;
                    font-size: 13px;
                }

                /* ── Input fields (glass effect) ── */
                QLineEdit {
                    font-family: Inter, -apple-system, "system-ui", "SF Pro Text", "Helvetica Neue", sans-serif;
                    border: 1px solid rgba(255, 255, 255, 80);
                    border-radius: 10px;
                    padding: 0 12px;
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(255, 255, 255, 128),
                        stop:1 rgba(255, 255, 255, 64)
                    );
                    color: #1d1d1f;
                    font-size: 13px;
                    min-height: 32px;
                }
                QLineEdit:focus {
                    border: 1px solid rgba(0, 113, 227, 180);
                }
                QLineEdit::placeholder {
                    color: rgba(29, 29, 31, 77);
                }

                /* ── Default button (glass / secondary) ── */
                QPushButton {
                    font-family: Inter, -apple-system, "system-ui", "SF Pro Text", "Helvetica Neue", sans-serif;
                    padding: 0 14px;
                    border-radius: 10px;
                    border: 1px solid rgba(255, 255, 255, 80);
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 255, 255, 153),
                        stop:1 rgba(255, 255, 255, 77)
                    );
                    color: #1d1d1f;
                    font-weight: 500;
                    font-size: 13px;
                    min-height: 30px;
                    max-height: 30px;
                }
                QPushButton:hover {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 255, 255, 179),
                        stop:1 rgba(255, 255, 255, 102)
                    );
                }
                QPushButton:pressed {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 255, 255, 128),
                        stop:1 rgba(255, 255, 255, 51)
                    );
                }

                /* ── Primary button (green gradient) ── */
                QPushButton#Primary {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(52, 199, 89, 217),
                        stop:1 rgba(40, 180, 75, 230)
                    );
                    color: white;
                    border: 1px solid rgba(40, 180, 75, 140);
                }
                QPushButton#Primary:hover {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(52, 199, 89, 240),
                        stop:1 rgba(40, 180, 75, 250)
                    );
                }
                QPushButton#Primary:pressed {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(40, 160, 65, 230),
                        stop:1 rgba(30, 140, 55, 245)
                    );
                }

                /* ── Danger button (red gradient) ── */
                QPushButton#Danger {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 59, 48, 217),
                        stop:1 rgba(220, 40, 32, 230)
                    );
                    color: white;
                    border: 1px solid rgba(220, 40, 32, 140);
                }
                QPushButton#Danger:hover {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 59, 48, 240),
                        stop:1 rgba(220, 40, 32, 250)
                    );
                }
                QPushButton#Danger:pressed {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(200, 40, 30, 230),
                        stop:1 rgba(180, 30, 25, 245)
                    );
                }

                QPushButton:disabled {
                    color: rgba(29, 29, 31, 102);
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 255, 255, 77),
                        stop:1 rgba(255, 255, 255, 38)
                    );
                    border-color: rgba(255, 255, 255, 40);
                }

                /* ── Dialogs ── */
                QDialog {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(246, 246, 246, 245),
                        stop:1 rgba(235, 235, 235, 245)
                    );
                    color: #1d1d1f;
                }

                /* ── Combo / Spin / Key editors ── */
                QComboBox, QSpinBox, QKeySequenceEdit {
                    border: 1px solid rgba(255, 255, 255, 80);
                    border-radius: 8px;
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 255, 255, 153),
                        stop:1 rgba(255, 255, 255, 77)
                    );
                    padding: 4px 8px;
                    min-height: 24px;
                }
                QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {
                    border-color: rgba(0, 113, 227, 180);
                }
                QComboBox::drop-down {
                    subcontrol-origin: padding;
                    subcontrol-position: top right;
                    width: 20px;
                    border: none;
                    background: transparent;
                }
                QDialogButtonBox QPushButton {
                    min-width: 80px;
                    padding: 6px 14px;
                }
                """
            )
        elif sys.platform.startswith("win"):
            self.window.setStyleSheet(
                """
                QWidget#MainWindow { background: #F3F3F3; color: #1F1F1F; }
                QFrame#Card { background: #FFFFFF; border: 1px solid #E1E1E1; border-radius: 8px; }
                QFrame#TimerCard { background: #FFFFFF; border: 1px solid #DADADA; border-radius: 8px; }
                QLabel#Meta { color: #5F5F5F; }
                QLabel#Timer { color: #111111; font-size: 28px; font-weight: 700; }
                QLabel#StateChip { border-radius: 8px; padding: 5px 8px; font-weight: 600; }
                QLabel#Status { border: 1px solid #E1E1E1; border-radius: 6px; padding: 8px 10px; background: #FFFFFF; }
                QLineEdit { border: 1px solid #C8C8C8; border-radius: 4px; padding: 6px 8px; background: white; }
                QPushButton { padding: 6px 10px; border-radius: 4px; border: 1px solid #CFCFCF; background: #FFFFFF; }
                QPushButton:hover { background: #F8F8F8; }
                QPushButton#Primary { background: #0078D4; color: white; border-color: #006CBE; }
                QPushButton#Primary:hover { background: #106EBE; }
                QPushButton#Danger { background: #C42B1C; color: white; border-color: #AA2418; }
                QPushButton#Danger:hover { background: #A4262C; }
                QPushButton:disabled { color: #8A8886; background: #F3F2F1; border-color: #E1DFDD; }
                QDialog { background: #F3F3F3; }
                """
            )

    def _build_ui(self) -> None:
        vbox = self.QVBoxLayout(self.window)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self.card = self.QFrame()
        self.card.setObjectName("Card")
        self._apply_shadow(self.card, blur=40, y=8, alpha=30)
        card_layout = self.QVBoxLayout(self.card)
        self.card_layout = card_layout
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        vbox.addWidget(self.card)

        self.session_card = self.QFrame()
        self.session_card.setObjectName("SessionCard")
        self._apply_shadow(self.session_card, blur=8, y=2, alpha=12)
        session_layout = self.QVBoxLayout(self.session_card)
        session_layout.setContentsMargins(16, 16, 16, 16)
        session_layout.setSpacing(6)
        card_layout.addWidget(self.session_card)

        self.run_label = self.QLabel()
        self.run_label.setStyleSheet("font-weight: 700; font-size: 16px;")
        session_layout.addWidget(self.run_label)
        self.session_label = self.QLabel()
        self.session_label.setObjectName("Meta")
        session_layout.addWidget(self.session_label)
        self.csv_label = self.QLabel()
        self.csv_label.setObjectName("Meta")
        session_layout.addWidget(self.csv_label)
        self.limit_label = self.QLabel()
        self.limit_label.setObjectName("Meta")
        session_layout.addWidget(self.limit_label)

        self.timer_card = self.QFrame()
        self.timer_card.setObjectName("TimerCard")
        self._apply_shadow(self.timer_card, blur=8, y=2, alpha=12)
        timer_layout = self.QVBoxLayout(self.timer_card)
        timer_layout.setContentsMargins(16, 16, 16, 16)
        timer_layout.setSpacing(8)
        card_layout.addWidget(self.timer_card)

        self.timer_label = self.QLabel()
        self.timer_label.setObjectName("Timer")
        timer_layout.addWidget(self.timer_label)

        self.state_chip = self.QLabel("Idle")
        self.state_chip.setObjectName("StateChip")
        self.state_chip.setAlignment(self.Qt.AlignLeft)
        timer_layout.addWidget(self.state_chip, 0, self.Qt.AlignLeft)

        self.compact_panel = self.QFrame()
        self.compact_panel.setObjectName("TimerCard")
        compact_layout = self.QVBoxLayout(self.compact_panel)
        self.compact_layout = compact_layout
        compact_layout.setContentsMargins(3, 2, 3, 2)
        compact_layout.setSpacing(0)
        self.compact_run_label = self.QLabel("Run #1")
        self.compact_run_label.setStyleSheet("font-weight: 700; font-size: 7px;")
        compact_layout.addWidget(self.compact_run_label)
        self.compact_timer_label = self.QLabel("00:00.00")
        self.compact_timer_label.setObjectName("Timer")
        self.compact_timer_label.setStyleSheet("font-size: 11px; font-weight: 700;")
        compact_layout.addWidget(self.compact_timer_label)
        self.compact_panel.hide()
        card_layout.addWidget(self.compact_panel)

        note_row = self.QHBoxLayout()
        note_row.setSpacing(8)
        self.note_label = self.QLabel("Note")
        self.note_label.setStyleSheet("font-size: 13px; font-weight: 500; color: rgba(29,29,31,204);")
        note_row.addWidget(self.note_label)
        self.note_entry = self.QLineEdit()
        self.note_entry.setPlaceholderText("Optional drop / note")
        note_row.addWidget(self.note_entry, 1)
        card_layout.addLayout(note_row)

        row1 = self.QHBoxLayout()
        row1.setSpacing(8)
        self.btn_start_stop = self.QPushButton()
        self.btn_start_stop.setObjectName("Primary")
        self.btn_start_stop.clicked.connect(lambda: self.handle_action("toggle_start_stop", "button"))  # type: ignore[attr-defined]
        row1.addWidget(self.btn_start_stop)
        self.btn_next_run = self.QPushButton()
        self.btn_next_run.clicked.connect(lambda: self.handle_action("next_run", "button"))  # type: ignore[attr-defined]
        row1.addWidget(self.btn_next_run)
        row1.addStretch(1)
        card_layout.addLayout(row1)

        row2 = self.QHBoxLayout()
        row2.setSpacing(8)
        self.btn_reset_timer = self.QPushButton()
        self.btn_reset_timer.clicked.connect(lambda: self.handle_action("reset_timer", "button"))  # type: ignore[attr-defined]
        row2.addWidget(self.btn_reset_timer)
        self.btn_reset_session = self.QPushButton()
        self.btn_reset_session.setObjectName("Danger")
        self.btn_reset_session.clicked.connect(lambda: self.handle_action("reset_session", "button"))  # type: ignore[attr-defined]
        row2.addWidget(self.btn_reset_session)
        self.btn_undo = self.QPushButton()
        self.btn_undo.clicked.connect(lambda: self.handle_action("undo_last", "button"))  # type: ignore[attr-defined]
        row2.addWidget(self.btn_undo)
        self.btn_settings = self.QPushButton("Settings")
        self.btn_settings.clicked.connect(self._open_settings_dialog)  # type: ignore[attr-defined]
        row2.addWidget(self.btn_settings)
        self.btn_hide_tray = self.QPushButton("Hide to Tray")
        self.btn_hide_tray.clicked.connect(self._hide_to_tray)  # type: ignore[attr-defined]
        row2.addWidget(self.btn_hide_tray)
        self.btn_mini_mode = self.QPushButton("Mini Mode")
        self.btn_mini_mode.clicked.connect(self._toggle_mini_mode)  # type: ignore[attr-defined]
        row2.addWidget(self.btn_mini_mode)
        row2.addStretch(1)
        card_layout.addLayout(row2)

        self.status_label = self.QLabel()
        self.status_label.setObjectName("Status")
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.status_label)

        self.help_label = self.QLabel("Global hotkeys are configurable in Settings")
        self.help_label.setObjectName("Help")
        self.help_label.setWordWrap(True)
        card_layout.addWidget(self.help_label)

        self._refresh_action_button_labels()

    def _setup_tray(self) -> None:
        try:
            if not self.QSystemTrayIcon.isSystemTrayAvailable():
                self.log.warning("system_tray_unavailable")
                return
            icon = self.window.windowIcon()
            if icon.isNull():
                icon = self.qt_app.style().standardIcon(self.QStyle.SP_ComputerIcon)
            tray = self.QSystemTrayIcon(icon, self.window)
            tray.setToolTip("D2 Runner")

            menu = self.QMenu()
            act_show = self.QAction("Show Window", menu)
            act_hide = self.QAction("Hide to Tray", menu)
            act_view_normal = self.QAction("Normal View", menu)
            act_view_compact = self.QAction("Compact View", menu)
            act_view_mini = self.QAction("Mini View", menu)
            act_quit = self.QAction("Quit", menu)
            act_show.triggered.connect(self._show_from_tray)  # type: ignore[attr-defined]
            act_hide.triggered.connect(self._hide_to_tray)  # type: ignore[attr-defined]
            act_view_normal.triggered.connect(lambda: self._set_overlay_mode("off"))  # type: ignore[attr-defined]
            act_view_compact.triggered.connect(lambda: self._set_overlay_mode("compact"))  # type: ignore[attr-defined]
            act_view_mini.triggered.connect(lambda: self._set_overlay_mode("mini"))  # type: ignore[attr-defined]
            act_quit.triggered.connect(self._quit_from_tray)  # type: ignore[attr-defined]
            menu.addAction(act_show)
            menu.addAction(act_hide)
            menu.addSeparator()
            menu.addAction(act_view_normal)
            menu.addAction(act_view_compact)
            menu.addAction(act_view_mini)
            menu.addSeparator()
            menu.addAction(act_quit)
            tray.setContextMenu(menu)
            tray.activated.connect(self._on_tray_activated)  # type: ignore[attr-defined]
            tray.show()
            self._tray = tray
            self._tray_enabled = True
            self.log.info("system_tray_enabled")
        except Exception:
            self.log.exception("system_tray_setup_failed")
            self._tray = None
            self._tray_enabled = False

    def _hide_to_tray(self) -> None:
        if not self._tray_enabled:
            self.log.warning("hide_to_tray_requested_but_unavailable")
            self.status_label.setText("System tray unavailable on this system.")
            self._refresh_visual_state()
            return
        self.window.hide()
        try:
            self._tray.showMessage("D2 Runner", "Running in tray. Use tray icon to restore.")
        except Exception:
            pass
        self.log.info("window_hidden_to_tray")

    def _show_from_tray(self) -> None:
        try:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            self._position_overlay()
            self.log.info("window_restored_from_tray")
        except Exception:
            self.log.exception("window_restore_from_tray_failed")

    def _toggle_tray_visibility(self) -> None:
        if self.window.isVisible():
            self._hide_to_tray()
        else:
            self._show_from_tray()

    def _on_tray_activated(self, reason) -> None:
        # Trigger/DoubleClick behavior varies by OS; accept both.
        try:
            trigger = self.QSystemTrayIcon.Trigger
            double_click = self.QSystemTrayIcon.DoubleClick
            if reason in (trigger, double_click):
                self._toggle_tray_visibility()
        except Exception:
            self.log.exception("tray_activate_handler_failed")

    def _quit_from_tray(self) -> None:
        self._quitting = True
        self.log.info("quit_requested_from_tray")
        self.qt_app.quit()

    def _apply_shadow(self, widget, blur: int, y: int, alpha: int) -> None:
        try:
            effect = self.QGraphicsDropShadowEffect(widget)
            effect.setBlurRadius(blur)
            effect.setOffset(0, y)
            effect.setColor(self.QColor(0, 0, 0, alpha))
            widget.setGraphicsEffect(effect)
        except Exception:
            return

    def _new_session_csv_path(self) -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        parent = self.base_csv_path.parent if str(self.base_csv_path.parent) else Path(".")
        stem = self.base_csv_path.stem or "runs"
        suffix = self.base_csv_path.suffix or ".csv"
        path = parent / f"{stem}_{ts}{suffix}"
        if not path.exists():
            return path
        for i in range(1, 1000):
            alt = parent / f"{stem}_{ts}_{i}{suffix}"
            if not alt.exists():
                return alt
        return parent / f"{stem}_{ts}_session{suffix}"

    def _rotate_session_csv(self) -> None:
        self.current_csv_path = self._new_session_csv_path()
        self.tracker.logger = CsvRunLogger(self.current_csv_path)
        self.log.info("session_csv_rotated path=%s", self.current_csv_path)

    def _limit_text(self) -> str:
        return f"Saved runs in session: {self.tracker.saved_runs_count}/{self.SESSION_RUN_LIMIT}"

    def _run_limit_reached(self) -> bool:
        return self.tracker.saved_runs_count >= self.SESSION_RUN_LIMIT

    def _run_limit_message(self) -> str:
        return f"Run limit {self.SESSION_RUN_LIMIT} reached. Create a new session."

    def _is_blocked_by_run_limit(self, action: str) -> bool:
        return self._run_limit_reached() and action in {"toggle_start_stop", "next_run", "reset_timer", "undo_last"}

    def _refresh_action_button_labels(self) -> None:
        self.btn_start_stop.setText(self._label_for("toggle_start_stop"))
        self.btn_next_run.setText(self._label_for("next_run"))
        self.btn_reset_timer.setText(self._label_for("reset_timer"))
        self.btn_reset_session.setText(self._label_for("reset_session"))
        self.btn_undo.setText(self._label_for("undo_last"))

    def _label_for(self, action: str) -> str:
        combo = self.controller_config.keyboard_map.get(action, "")
        h = human_combo_label(combo)
        title = ACTION_TITLES.get(action, action)
        return f"{h} {title}".strip() if h else title

    def _update_labels(self) -> None:
        self.run_label.setText(f"Run #{self.tracker.run_number}")
        self.compact_run_label.setText(f"Run #{self.tracker.run_number}")
        self.session_label.setText(f"Session {self.tracker.session_id}")
        self.csv_label.setText(f"CSV {self.current_csv_path.name}")
        self.limit_label.setText(self._limit_text())
        self.timer_label.setText(self.tracker.formatted_elapsed())
        self.compact_timer_label.setText(self.tracker.formatted_elapsed())
        state = "Running" if self.tracker.is_running else (self.state_chip.text() or "Idle")
        self.state_chip.setText(state)
        if hasattr(self, "btn_mini_mode"):
            self.btn_mini_mode.setText("Normal View" if self.overlay_mode == "mini" else "Mini Mode")

    def _update_control_states(self) -> None:
        blocked = self._run_limit_reached()
        for w in [self.btn_start_stop, self.btn_next_run, self.btn_reset_timer, self.btn_undo, self.note_entry]:
            w.setEnabled(not blocked)
        self.btn_reset_session.setEnabled(True)
        self.btn_settings.setEnabled(True)
        if hasattr(self, "btn_hide_tray"):
            self.btn_hide_tray.setEnabled(True)
        if hasattr(self, "btn_mini_mode"):
            self.btn_mini_mode.setEnabled(True)
        self.limit_label.setText(self._limit_text())

    def _refresh_visual_state(self) -> None:
        blocked = self._run_limit_reached()
        state_text = (self.state_chip.text() or "").lower()
        is_mac = sys.platform == "darwin"

        if blocked:
            chip_bg, chip_fg = "rgba(253, 236, 236, 200)", "#A61B1B"
        elif "running" in state_text:
            chip_bg, chip_fg = "rgba(232, 245, 233, 200)", "#1B5E20"
        elif "paused" in state_text:
            chip_bg, chip_fg = "rgba(255, 248, 225, 200)", "#8D6E00"
        else:
            chip_bg, chip_fg = "rgba(243, 244, 246, 140)", "#86868b"

        self.state_chip.setStyleSheet(
            f"QLabel#StateChip {{ background: {chip_bg}; color: {chip_fg}; border-radius: 10px;"
            f" padding: 4px 12px; font-weight: 600; font-size: 12px; max-width: 120px; }}"
        )

        s = (self.status_label.text() or "").lower()
        if "failed" in s or "error" in s or "limit" in s or "blocked" in s:
            self.status_label.setStyleSheet(
                "QLabel#Status { background: rgba(253,236,236,160); color: #A61B1B;"
                " border: 1px solid rgba(243,198,198,120); border-radius: 14px; padding: 8px 12px; }"
            )
        elif "saved run" in s or "started" in s or "active" in s:
            self.status_label.setStyleSheet(
                "QLabel#Status { background: rgba(234,247,238,160); color: #146C2E;"
                " border: 1px solid rgba(202,233,212,120); border-radius: 14px; padding: 8px 12px; }"
            )
        else:
            self.status_label.setStyleSheet("")

    def _rebuild_qt_shortcuts(self) -> None:
        for sc in self._qt_shortcuts:
            try:
                sc.setEnabled(False)
                sc.deleteLater()
            except Exception:
                pass
        self._qt_shortcuts = []

        for action in ACTION_ORDER:
            combo = self.controller_config.keyboard_map.get(action, "")
            portable = _internal_combo_to_qt_portable(combo)
            if not portable:
                continue
            seq = self.QKeySequence(portable)
            if seq.isEmpty():
                continue
            sc = self.QShortcut(seq, self.window)
            sc.setContext(self.Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda a=action: self.handle_action(a, "local_hotkey"))  # type: ignore[attr-defined]
            self._qt_shortcuts.append(sc)

    def _drain_queue(self) -> None:
        while True:
            try:
                source, action = self.command_queue.get_nowait()
            except Empty:
                break
            self.handle_action(action, source)

    def _tick(self) -> None:
        self._drain_queue()
        self.timer_label.setText(self.tracker.formatted_elapsed())

    def _find_duplicate_bindings(self, keyboard_map: dict[str, str]) -> tuple[str, list[str]] | None:
        seen: dict[str, list[str]] = {}
        for action, combo in keyboard_map.items():
            if not combo:
                continue
            seen.setdefault(combo, []).append(ACTION_TITLES.get(action, action))
        for combo, actions in seen.items():
            if len(actions) > 1:
                return combo, actions
        return None

    def _find_duplicate_dpad(self, selected_dirs: dict[str, str]) -> tuple[str, list[str]] | None:
        seen: dict[str, list[str]] = {}
        for action, direction in selected_dirs.items():
            if direction == "none":
                continue
            seen.setdefault(direction, []).append(ACTION_TITLES.get(action, action))
        for direction, actions in seen.items():
            if len(actions) > 1:
                return direction, actions
        return None

    def _open_settings_dialog(self) -> None:
        dlg = self.QDialog(self.window)
        dlg.setWindowTitle("Settings")
        dlg.setModal(True)
        dlg.resize(640, 520)

        # ── Settings dialog stylesheet ──
        _lbl_style = "font-size: 13px; font-weight: 400; color: rgba(29,29,31,179);"
        _header_style = "font-size: 11px; font-weight: 600; color: rgba(29,29,31,115); letter-spacing: 0.5px;"
        _row_label_style = "font-size: 13px; font-weight: 500; color: rgba(29,29,31,204);"
        dlg.setStyleSheet(
            "QDialog {"
            "  background: #f5f5f7;"
            "  color: #1d1d1f;"
            "  font-family: Inter, -apple-system, 'SF Pro Text', sans-serif;"
            "}"
            "QLabel { font-family: Inter, -apple-system, 'SF Pro Text', sans-serif; }"
            "QCheckBox {"
            "  font-family: Inter, -apple-system, 'SF Pro Text', sans-serif;"
            "  font-size: 13px; font-weight: 500; color: rgba(29,29,31,204);"
            "  spacing: 6px;"
            "}"
            "QSpinBox, QKeySequenceEdit, QComboBox {"
            "  font-family: Inter, -apple-system, 'SF Pro Text', sans-serif;"
            "  font-size: 13px;"
            "  border: 1px solid rgba(0,0,0,0.08);"
            "  border-radius: 6px;"
            "  background: #ffffff;"
            "  padding: 4px 8px;"
            "  min-height: 28px;"
            "  max-height: 28px;"
            "  color: #1d1d1f;"
            "}"
            "QSpinBox:focus, QKeySequenceEdit:focus, QComboBox:focus {"
            "  border: 1px solid rgba(0,122,255,0.6);"
            "}"
            "QComboBox::drop-down {"
            "  subcontrol-origin: padding;"
            "  subcontrol-position: top right;"
            "  width: 24px;"
            "  border: none;"
            "  background: transparent;"
            "}"
            "QSpinBox::up-button, QSpinBox::down-button {"
            "  width: 20px;"
            "  border: none;"
            "  background: transparent;"
            "}"
            "QFrame#SettingsSep {"
            "  background: rgba(0,0,0,0.08);"
            "  max-height: 1px;"
            "  min-height: 1px;"
            "}"
            "QPushButton {"
            "  font-family: Inter, -apple-system, 'SF Pro Text', sans-serif;"
            "  font-size: 13px; font-weight: 500;"
            "  border-radius: 8px;"
            "  border: 1px solid rgba(0,0,0,0.08);"
            "  padding: 0 20px;"
            "  min-height: 30px; max-height: 30px;"
            "  background: #ffffff;"
            "  color: #1d1d1f;"
            "}"
            "QPushButton:hover {"
            "  background: #f0f0f0;"
            "}"
            "QPushButton#SaveBtn {"
            "  background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "    stop:0 rgba(0,122,255,230), stop:1 rgba(0,100,220,242));"
            "  color: white;"
            "  border: none;"
            "}"
            "QPushButton#SaveBtn:hover {"
            "  background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "    stop:0 rgba(0,122,255,250), stop:1 rgba(0,100,220,255));"
            "}"
        )

        layout = self.QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # ── Top section: controller settings ──
        top_grid = self.QGridLayout()
        top_grid.setVerticalSpacing(14)
        top_grid.setHorizontalSpacing(16)
        top_grid.setColumnMinimumWidth(0, 140)
        top_grid.setColumnStretch(1, 1)
        layout.addLayout(top_grid)

        enable_cb = self.QCheckBox("Enable controller")
        enable_cb.setChecked(self.controller_config.enabled)
        top_grid.addWidget(enable_cb, 0, 0, 1, 2)

        lbl_dev = self.QLabel("Device index")
        lbl_dev.setStyleSheet(_lbl_style)
        top_grid.addWidget(lbl_dev, 1, 0)
        device_spin = self.QSpinBox()
        device_spin.setRange(0, 32)
        device_spin.setValue(self.controller_config.device_index)
        top_grid.addWidget(device_spin, 1, 1)

        lbl_hat = self.QLabel("Hat index (D-pad)")
        lbl_hat.setStyleSheet(_lbl_style)
        top_grid.addWidget(lbl_hat, 2, 0)
        hat_spin = self.QSpinBox()
        hat_spin.setRange(0, 16)
        hat_spin.setValue(self.controller_config.hat_index)
        top_grid.addWidget(hat_spin, 2, 1)

        lbl_guard = self.QLabel("Repeat guard ms")
        lbl_guard.setStyleSheet(_lbl_style)
        top_grid.addWidget(lbl_guard, 3, 0)
        guard_spin = self.QSpinBox()
        guard_spin.setRange(0, 5000)
        guard_spin.setValue(self.controller_config.repeat_guard_ms)
        top_grid.addWidget(guard_spin, 3, 1)

        # ── Separator ──
        sep = self.QFrame()
        sep.setObjectName("SettingsSep")
        sep.setFrameShape(self.QFrame.HLine)
        layout.addWidget(sep)

        # ── Action bindings table ──
        grid = self.QGridLayout()
        grid.setVerticalSpacing(14)
        grid.setHorizontalSpacing(16)
        grid.setColumnMinimumWidth(0, 140)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 80)
        layout.addLayout(grid)

        row = 0
        h_action = self.QLabel("ACTION")
        h_action.setStyleSheet(_header_style)
        grid.addWidget(h_action, row, 0)
        h_kbd = self.QLabel("KEYBOARD")
        h_kbd.setStyleSheet(_header_style)
        grid.addWidget(h_kbd, row, 1)
        h_dpad = self.QLabel("D-PAD")
        h_dpad.setStyleSheet(_header_style)
        grid.addWidget(h_dpad, row, 2)
        row += 1

        inv_dpad = {action: "none" for action in ACTION_ORDER}
        for direction, action in self.controller_config.dpad_map.items():
            if action in inv_dpad:
                inv_dpad[action] = direction

        key_edits: dict[str, object] = {}
        dpad_boxes: dict[str, object] = {}
        dpad_choices = ["none", "up", "right", "down", "left"]
        for action in ACTION_ORDER:
            act_lbl = self.QLabel(ACTION_TITLES[action])
            act_lbl.setStyleSheet(_row_label_style)
            grid.addWidget(act_lbl, row, 0)
            key_edit = self.QKeySequenceEdit()
            key_edit.setClearButtonEnabled(True)
            key_edit.setKeySequence(self.QKeySequence(_internal_combo_to_qt_portable(self.controller_config.keyboard_map.get(action, ""))))
            grid.addWidget(key_edit, row, 1)
            key_edits[action] = key_edit

            dbox = self.QComboBox()
            dbox.addItems(dpad_choices)
            dbox.setCurrentText(inv_dpad.get(action, "none"))
            grid.addWidget(dbox, row, 2)
            dpad_boxes[action] = dbox
            row += 1

        info = self.QLabel("Changes apply immediately after Save. Duplicate bindings are blocked.")
        info.setStyleSheet("font-size: 12px; color: rgba(29,29,31,100); font-weight: 400;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # ── Custom button row (Cancel + Save) ──
        btn_row = self.QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = self.QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)  # type: ignore[attr-defined]
        btn_row.addWidget(btn_cancel)
        btn_save = self.QPushButton("Save")
        btn_save.setObjectName("SaveBtn")
        btn_row.addWidget(btn_save)
        btn_row.setSpacing(10)
        layout.addLayout(btn_row)

        def _save() -> None:
            try:
                keyboard_map: dict[str, str] = {}
                for action in ACTION_ORDER:
                    portable = key_edits[action].keySequence().toString(self.QKeySequence.PortableText)
                    keyboard_map[action] = _qt_portable_to_internal(portable)
                dup = self._find_duplicate_bindings(keyboard_map)
                if dup:
                    combo, actions = dup
                    raise ValueError(f"Duplicate keyboard binding '{combo}' for: {', '.join(actions)}")

                selected_dirs = {action: dpad_boxes[action].currentText() for action in ACTION_ORDER}
                dup_d = self._find_duplicate_dpad(selected_dirs)
                if dup_d:
                    direction, actions = dup_d
                    raise ValueError(f"Duplicate D-pad direction '{direction}' for: {', '.join(actions)}")
                dpad_map = {"up": "none", "right": "none", "down": "none", "left": "none"}
                for action, direction in selected_dirs.items():
                    if direction in dpad_map:
                        dpad_map[direction] = action

                new_cfg = ControllerConfig(
                    enabled=enable_cb.isChecked(),
                    device_index=device_spin.value(),
                    hat_index=hat_spin.value(),
                    repeat_guard_ms=guard_spin.value(),
                    dpad_map=dpad_map,
                    keyboard_map=keyboard_map,
                )
                save_controller_config(self.controller_config_path, new_cfg, self.log)
                self.controller_config = load_controller_config(self.controller_config_path, self.log)
                self.hotkeys.reload_bindings(self.controller_config.keyboard_map)
                self.controller.reload(self.controller_config)
                self._rebuild_qt_shortcuts()
                self._refresh_action_button_labels()
                self.status_label.setText("Settings saved and reloaded.")
                self._refresh_visual_state()
                dlg.accept()
            except Exception as exc:
                self.log.exception("qt_settings_save_failed")
                self.QMessageBox.critical(dlg, "Settings Error", str(exc))

        btn_save.clicked.connect(_save)  # type: ignore[attr-defined]
        dlg.exec()

    def handle_action(self, action: str, source: str = "unknown") -> None:
        self.log.info("action_received source=%s action=%s", source, action)
        if self._is_blocked_by_run_limit(action):
            self.status_label.setText(self._run_limit_message())
            self._update_control_states()
            self._refresh_visual_state()
            self.log.warning(
                "action_blocked_by_run_limit source=%s action=%s saved_runs=%s limit=%s",
                source,
                action,
                self.tracker.saved_runs_count,
                self.SESSION_RUN_LIMIT,
            )
            return

        if action == "toggle_start_stop":
            result = self.tracker.toggle_start_stop()
            self.state_chip.setText("Running" if self.tracker.is_running else "Paused")
            self.status_label.setText("Timer started" if result == "started" else "Timer stopped")

        elif action == "next_run":
            note = self.note_entry.text()
            record, result = self.tracker.next_run(note=note, max_saved_runs=self.SESSION_RUN_LIMIT)
            self.state_chip.setText("Running" if self.tracker.is_running else "Idle")
            if result == "started_first_run":
                self.status_label.setText("Started run #1 (no CSV row yet).")
            elif result == "saved_limit_reached":
                self.note_entry.clear()
                self.status_label.setText(
                    f"Saved run #{record.run_number}: {record.duration_sec:.3f}s. {self._run_limit_message()}"
                )
            else:
                self.note_entry.clear()
                self.status_label.setText(
                    f"Saved run #{record.run_number}: {record.duration_sec:.3f}s; started run #{self.tracker.run_number}."
                )

        elif action == "reset_timer":
            self.tracker.reset_timer()
            self.state_chip.setText("Idle")
            self.status_label.setText("Current timer reset (run number unchanged).")

        elif action == "reset_session":
            self._rotate_session_csv()
            self.tracker.reset_session()
            self.note_entry.clear()
            self.state_chip.setText("Idle")
            self.status_label.setText(f"New session started; CSV {self.current_csv_path.name}; counter reset to run #1.")

        elif action == "undo_last":
            undone, reason = self.tracker.undo_last_run()
            if undone:
                self.status_label.setText("Removed last CSV row for current session.")
            elif reason == "active_run_present":
                self.status_label.setText("Undo blocked: reset current run first, then Undo.")
            else:
                self.status_label.setText("Nothing to undo for this session.")

        self._update_labels()
        self._update_control_states()
        self._refresh_visual_state()
        self.log.info(
            "action_applied source=%s action=%s run=%s session=%s running=%s elapsed_ms=%s",
            source,
            action,
            self.tracker.run_number,
            self.tracker.session_id,
            self.tracker.is_running,
            self.tracker.current_elapsed_ms(),
        )

    def run(self) -> None:
        self.hotkeys.start()
        self.controller.start()
        self.status_label.setText(f"Ready. CSV: {self.current_csv_path.name}")
        if self.hotkeys.available:
            self.status_label.setText(f"Global hotkeys active. CSV: {self.current_csv_path.name}")
        elif self.hotkeys.error:
            self.status_label.setText(f"{self.hotkeys.error} (window hotkeys still work)")
            self.log.warning("global_hotkeys_unavailable error=%s", self.hotkeys.error)
        else:
            self.log.warning("global_hotkeys_unavailable error=unknown")
        if self.controller.error:
            self.log.warning("controller_unavailable error=%s", self.controller.error)
        self._update_labels()
        self._update_control_states()
        self._refresh_visual_state()

        self.window.show()
        self._position_overlay()
        try:
            self.qt_app.exec()
        finally:
            if self._tray is not None:
                try:
                    self._tray.hide()
                except Exception:
                    pass
            self.hotkeys.stop()
            self.controller.stop()
