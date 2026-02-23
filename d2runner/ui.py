from __future__ import annotations

import logging
from pathlib import Path
from queue import Empty, Queue
import sys
import tkinter as tk
from tkinter import ttk

from .controller import ControllerBackend, ControllerConfig, VALID_ACTIONS, load_controller_config, save_controller_config
from .core import CsvRunLogger, RunTracker
from .hotkeys import ACTION_ORDER, ACTION_TITLES, HotkeyBackend, human_combo_label, normalize_combo_string


class D2RunnerApp:
    SESSION_RUN_LIMIT = 500

    def __init__(self, csv_path: Path, controller_config_path: Path) -> None:
        self.root = tk.Tk()
        self.root.title("D2 Runner")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.ttk_style = ttk.Style()
        self.ui = self._build_platform_ui_theme()
        self._configure_platform_styles()

        self.log = logging.getLogger("d2runner")
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

        self.timer_var = tk.StringVar(value=self.tracker.formatted_elapsed())
        self.run_var = tk.StringVar(value=f"Run #{self.tracker.run_number}")
        self.session_var = tk.StringVar(value=f"Session {self.tracker.session_id}")
        self.csv_var = tk.StringVar(value=f"CSV {self.current_csv_path.name}")
        self.state_var = tk.StringVar(value="Idle")
        self.status_var = tk.StringVar(value="Ready")
        self.note_var = tk.StringVar()
        self.limit_var = tk.StringVar(value=self._limit_text())

        self._build_ui()
        self._bound_local_sequences: set[str] = set()
        self._local_debug_binds_installed = False
        self._bind_local_hotkeys()
        self._refresh_visual_state()

    def _build_platform_ui_theme(self) -> dict[str, object]:
        if sys.platform == "darwin":
            return {
                "font_ui": ("Inter", 13),
                "font_small": ("Inter", 13),
                "font_mono": ("SF Mono", 14),
                "font_timer": ("SF Mono", 48, "bold"),
                "bg_app": "#e8c4a0",       # Simplified background color instead of gradient
                "bg_card": "#f6f6f6",
                "bg_subtle": "#ffffff",
                "fg_primary": "#1d1d1f",
                "fg_secondary": "#86868b",
                "fg_muted": "#86868b",
                "line": "#d4d4d4",
                "accent": "#34c759",       
                "accent_press": "#30b350",
                "danger": "#ff3b30",       
                "danger_press": "#e6362c",
                "success_bg": "#ffffff",
                "success_fg": "#1d1d1f",
                "idle_bg": "#f0f0f0",
                "idle_fg": "#1d1d1f",
                "warn_bg": "#ffffff",
                "warn_fg": "#1d1d1f",
                "blocked_bg": "#ffffff",
                "blocked_fg": "#ff3b30",
                "status_bg": "#ffffff",
                "status_fg": "#1d1d1f",
                "entry_bg": "#ffffff",
            }
        if sys.platform.startswith("win"):
            return {
                "font_ui": ("Segoe UI", 10),
                "font_small": ("Segoe UI", 9),
                "font_mono": ("Consolas", 11),
                "font_timer": ("Consolas", 24, "bold"),
                "bg_app": "#F3F3F3",
                "bg_card": "#FFFFFF",
                "bg_subtle": "#FAFAFA",
                "fg_primary": "#1F1F1F",
                "fg_secondary": "#5F5F5F",
                "fg_muted": "#767676",
                "line": "#E1E1E1",
                "accent": "#0078D4",
                "accent_press": "#106EBE",
                "danger": "#C42B1C",
                "danger_press": "#A4262C",
                "success_bg": "#E8F3EC",
                "success_fg": "#107C41",
                "idle_bg": "#F3F2F1",
                "idle_fg": "#323130",
                "warn_bg": "#FFF4CE",
                "warn_fg": "#8A6A00",
                "blocked_bg": "#FDE7E9",
                "blocked_fg": "#A80000",
                "status_bg": "#F8F8F8",
                "status_fg": "#201F1E",
                "entry_bg": "#FFFFFF",
            }
        return {
            "font_ui": ("TkDefaultFont", 10),
            "font_small": ("TkDefaultFont", 9),
            "font_mono": ("Courier", 11),
            "font_timer": ("Courier", 22, "bold"),
            "bg_app": "#EFEFEF",
            "bg_card": "#FFFFFF",
            "bg_subtle": "#F6F6F6",
            "fg_primary": "#222222",
            "fg_secondary": "#555555",
            "fg_muted": "#777777",
            "line": "#DDDDDD",
            "accent": "#1E88E5",
            "accent_press": "#1565C0",
            "danger": "#C62828",
            "danger_press": "#B71C1C",
            "success_bg": "#E8F5E9",
            "success_fg": "#2E7D32",
            "idle_bg": "#F1F3F4",
            "idle_fg": "#444444",
            "warn_bg": "#FFF8E1",
            "warn_fg": "#8D6E00",
            "blocked_bg": "#FDECEC",
            "blocked_fg": "#A61B1B",
            "status_bg": "#F8F8F8",
            "status_fg": "#2A2A2A",
            "entry_bg": "#FFFFFF",
        }

    def _configure_platform_styles(self) -> None:
        self.root.configure(bg=self.ui["bg_app"])
        preferred_theme = "aqua" if sys.platform == "darwin" else "vista" if sys.platform.startswith("win") else None
        if preferred_theme:
            try:
                self.ttk_style.theme_use(preferred_theme)
            except Exception:
                pass
        try:
            self.ttk_style.configure("D2.TFrame", background=self.ui["bg_card"])
            self.ttk_style.configure("D2Sub.TFrame", background=self.ui["bg_subtle"])
            self.ttk_style.configure("D2.TLabel", background=self.ui["bg_card"], foreground=self.ui["fg_primary"])
            self.ttk_style.configure("D2Sub.TLabel", background=self.ui["bg_card"], foreground=self.ui["fg_secondary"])
            self.ttk_style.configure("D2.TCheckbutton", background=self.ui["bg_card"], foreground=self.ui["fg_primary"])
        except Exception:
            pass

    def _tk_button_colors(self, role: str = "neutral") -> dict[str, object]:
        if role == "accent":
            bg = self.ui["accent"]
            active = self.ui["accent_press"]
            fg = "#FFFFFF"
        elif role == "danger":
            bg = self.ui["danger"]
            active = self.ui["danger_press"]
            fg = "#FFFFFF"
        else:
            bg = self.ui["bg_subtle"]
            active = self.ui["line"]
            fg = self.ui["fg_primary"]
        return {
            "bg": bg,
            "fg": fg,
            "activebackground": active,
            "activeforeground": fg,
            "relief": tk.FLAT,
            "bd": 0,
            "highlightthickness": 0,
            "font": self.ui["font_small"],
            "padx": 10,
            "pady": 6,
            "cursor": "hand2",
        }

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        self.frame = tk.Frame(
            self.root,
            bg=self.ui["bg_card"],
            bd=1,
            relief=tk.SOLID,
            highlightthickness=1,
            highlightbackground=self.ui["line"],
            highlightcolor=self.ui["line"],
        )
        self.frame.pack(padx=12, pady=12)
        self.frame.grid_columnconfigure(0, weight=1)

        self.header_frame = tk.Frame(self.frame, bg=self.ui["bg_card"])
        self.header_frame.grid(row=0, column=0, sticky="we", padx=8, pady=(8, 2))
        self.header_frame.grid_columnconfigure(0, weight=1)

        self.run_label = tk.Label(
            self.header_frame,
            textvariable=self.run_var,
            font=(self.ui["font_ui"][0], self.ui["font_ui"][1], "bold"),
            bg=self.ui["bg_card"],
            fg=self.ui["fg_primary"],
        )
        self.run_label.grid(
            row=0, column=0, sticky="w", **pad
        )
        self.session_label = tk.Label(
            self.header_frame,
            textvariable=self.session_var,
            font=self.ui["font_small"],
            bg=self.ui["bg_card"],
            fg=self.ui["fg_secondary"],
        )
        self.session_label.grid(
            row=1, column=0, sticky="w", **pad
        )
        self.csv_label = tk.Label(
            self.header_frame,
            textvariable=self.csv_var,
            font=self.ui["font_small"],
            bg=self.ui["bg_card"],
            fg=self.ui["fg_secondary"],
        )
        self.csv_label.grid(
            row=2, column=0, sticky="w", **pad
        )
        self.limit_label = tk.Label(
            self.header_frame,
            textvariable=self.limit_var,
            font=self.ui["font_small"],
            bg=self.ui["bg_card"],
            fg=self.ui["fg_secondary"],
        )
        self.limit_label.grid(
            row=3, column=0, sticky="w", **pad
        )
        self.timer_card = tk.Frame(
            self.frame,
            bg=self.ui["bg_subtle"],
            highlightthickness=1,
            highlightbackground=self.ui["line"],
        )
        self.timer_card.grid(row=1, column=0, sticky="we", padx=10, pady=(6, 6))
        self.timer_label = tk.Label(
            self.timer_card,
            textvariable=self.timer_var,
            font=self.ui["font_timer"],
            bg=self.ui["bg_subtle"],
            fg=self.ui["fg_primary"],
            anchor="w",
            padx=10,
            pady=8,
        )
        self.timer_label.pack(fill="x")
        self.state_label = tk.Label(
            self.timer_card,
            textvariable=self.state_var,
            font=self.ui["font_small"],
            bg=self.ui["idle_bg"],
            fg=self.ui["idle_fg"],
            padx=10,
            pady=4,
            anchor="w",
        )
        self.state_label.pack(fill="x", padx=10, pady=(0, 10))

        note_row = tk.Frame(self.frame, bg=self.ui["bg_card"])
        note_row.grid(row=2, column=0, sticky="we", padx=10, pady=6)
        tk.Label(
            note_row,
            text="Note",
            font=self.ui["font_small"],
            bg=self.ui["bg_card"],
            fg=self.ui["fg_secondary"],
        ).pack(side="left")
        self.note_entry = tk.Entry(
            note_row,
            textvariable=self.note_var,
            width=30,
            bg=self.ui["entry_bg"],
            fg=self.ui["fg_primary"],
            insertbackground=self.ui["fg_primary"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.ui["line"],
            highlightcolor=self.ui["accent"],
            font=self.ui["font_small"],
        )
        self.note_entry.pack(side="left", padx=(4, 0))

        buttons = tk.Frame(self.frame, bg=self.ui["bg_card"])
        buttons.grid(row=3, column=0, sticky="we", padx=10, pady=(6, 4))
        self.btn_start_stop = tk.Button(
            buttons,
            text=self._label_for("toggle_start_stop"),
            command=lambda: self.handle_action("toggle_start_stop", source="button"),
            **self._tk_button_colors("accent"),
        )
        self.btn_start_stop.pack(side="left")
        self.btn_next_run = tk.Button(
            buttons,
            text=self._label_for("next_run"),
            command=lambda: self.handle_action("next_run", source="button"),
            **self._tk_button_colors("neutral"),
        )
        self.btn_next_run.pack(side="left", padx=(4, 0))

        buttons2 = tk.Frame(self.frame, bg=self.ui["bg_card"])
        buttons2.grid(row=4, column=0, sticky="we", padx=10, pady=(0, 6))
        self.btn_reset_timer = tk.Button(
            buttons2,
            text=self._label_for("reset_timer"),
            command=lambda: self.handle_action("reset_timer", source="button"),
            **self._tk_button_colors("neutral"),
        )
        self.btn_reset_timer.pack(side="left")
        self.btn_reset_session = tk.Button(
            buttons2,
            text=self._label_for("reset_session"),
            command=lambda: self.handle_action("reset_session", source="button"),
            **self._tk_button_colors("danger"),
        )
        self.btn_reset_session.pack(side="left", padx=(4, 0))
        self.btn_undo = tk.Button(
            buttons2,
            text=self._label_for("undo_last"),
            command=lambda: self.handle_action("undo_last", source="button"),
            **self._tk_button_colors("neutral"),
        )
        self.btn_undo.pack(side="left", padx=(4, 0))
        self.btn_settings = tk.Button(
            buttons2,
            text="Settings",
            command=self._open_settings_dialog,
            **self._tk_button_colors("neutral"),
        )
        self.btn_settings.pack(side="left", padx=(8, 0))

        self.status_label = tk.Label(
            self.frame,
            textvariable=self.status_var,
            anchor="w",
            justify="left",
            wraplength=360,
            font=self.ui["font_small"],
            bg=self.ui["status_bg"],
            fg=self.ui["status_fg"],
            padx=10,
            pady=8,
            highlightthickness=1,
            highlightbackground=self.ui["line"],
        )
        self.status_label.grid(row=5, column=0, sticky="we", padx=10, pady=(2, 4))

        self.help_label = tk.Label(
            self.frame,
            text=self._hotkeys_help_text(),
            font=self.ui["font_small"],
            fg=self.ui["fg_muted"],
            bg=self.ui["bg_card"],
            justify="left",
        )
        self.help_label.grid(row=6, column=0, sticky="w", padx=10, pady=(0, 10))

    def _bind_local_hotkeys(self) -> None:
        for seq in getattr(self, "_bound_local_sequences", set()):
            try:
                self.root.unbind(seq)
            except Exception:
                pass
        self._bound_local_sequences = set()

        bindings = self._local_tk_bindings()
        for sequence in bindings["toggle_start_stop"]:
            self.root.bind(sequence, lambda _e: self.handle_action("toggle_start_stop", source="local_hotkey"))
            self._bound_local_sequences.add(sequence)
        for sequence in bindings["next_run"]:
            self.root.bind(sequence, lambda _e: self.handle_action("next_run", source="local_hotkey"))
            self._bound_local_sequences.add(sequence)
        for sequence in bindings["reset_timer"]:
            self.root.bind(sequence, lambda _e: self.handle_action("reset_timer", source="local_hotkey"))
            self._bound_local_sequences.add(sequence)
        for sequence in bindings["reset_session"]:
            self.root.bind(sequence, lambda _e: self.handle_action("reset_session", source="local_hotkey"))
            self._bound_local_sequences.add(sequence)
        for sequence in bindings["undo_last"]:
            self.root.bind(sequence, lambda _e: self.handle_action("undo_last", source="local_hotkey"))
            self._bound_local_sequences.add(sequence)
        if not self._local_debug_binds_installed:
            self.root.bind_all("<KeyPress>", self._log_local_keypress, add="+")
            self.root.bind_all("<KeyRelease>", self._log_local_keyrelease, add="+")
            self._local_debug_binds_installed = True
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _local_tk_bindings(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for action in ACTION_ORDER:
            combo = self.controller_config.keyboard_map.get(action, "")
            seqs = self._tk_sequences_for_combo(combo)
            out[action] = seqs
        return out

    def _tk_sequences_for_combo(self, combo: str) -> list[str]:
        combo = normalize_combo_string(combo)
        if not combo:
            return []
        parts = combo.split("+")
        if len(parts) < 1:
            return []
        key = parts[-1]
        mods = parts[:-1]
        mod_map = {"cmd": "Command", "alt": "Option", "ctrl": "Control", "shift": "Shift"}
        tk_mods = [mod_map.get(m) for m in mods if mod_map.get(m)]
        if not key:
            return []
        if key.startswith("f") and key[1:].isdigit():
            seq = "<" + "-".join([*tk_mods, key.upper()]) + ">"
            return [seq]
        key_part = f"Key-{key}"
        seq = "<" + "-".join([*tk_mods, key_part]) + ">"
        seqs = [seq]
        if sys.platform == "darwin" and "Option" in tk_mods:
            seqs.append(seq.replace("Option", "Alt"))
        return list(dict.fromkeys(seqs))

    def _label_for(self, action: str) -> str:
        combo = self.controller_config.keyboard_map.get(action, "")
        human = human_combo_label(combo)
        title = ACTION_TITLES.get(action, action)
        return f"{human} {title}".strip() if human else title

    def _refresh_action_button_labels(self) -> None:
        self.btn_start_stop.configure(text=self._label_for("toggle_start_stop"))
        self.btn_next_run.configure(text=self._label_for("next_run"))
        self.btn_reset_timer.configure(text=self._label_for("reset_timer"))
        self.btn_reset_session.configure(text=self._label_for("reset_session"))
        self.btn_undo.configure(text=self._label_for("undo_last"))

    def _hotkeys_help_text(self) -> str:
        if sys.platform == "darwin":
            return "Global hotkeys are configurable in Settings (needs macOS Accessibility permission)"
        return "Global hotkeys are configurable in Settings"

    def _on_close(self) -> None:
        self.hotkeys.stop()
        self.controller.stop()
        self.root.destroy()

    def _refresh_visual_state(self) -> None:
        blocked = self._run_limit_reached()
        state_text = (self.state_var.get() or "").strip().lower()
        if blocked:
            state_bg, state_fg = self.ui["blocked_bg"], self.ui["blocked_fg"]
        elif state_text == "running":
            state_bg, state_fg = self.ui["success_bg"], self.ui["success_fg"]
        elif state_text == "paused":
            state_bg, state_fg = self.ui["warn_bg"], self.ui["warn_fg"]
        else:
            state_bg, state_fg = self.ui["idle_bg"], self.ui["idle_fg"]
        self.state_label.configure(bg=state_bg, fg=state_fg)

        status_text = self.status_var.get() or ""
        status_bg = self.ui["status_bg"]
        status_fg = self.ui["status_fg"]
        lower = status_text.lower()
        if "failed" in lower or "error" in lower:
            status_bg, status_fg = self.ui["blocked_bg"], self.ui["blocked_fg"]
        elif "limit" in lower or "blocked" in lower:
            status_bg, status_fg = self.ui["blocked_bg"], self.ui["blocked_fg"]
        elif "saved run" in lower or "started" in lower or "active" in lower:
            status_bg, status_fg = self.ui["success_bg"], self.ui["success_fg"]
        self.status_label.configure(bg=status_bg, fg=status_fg)

        if blocked:
            self.timer_card.configure(highlightbackground=self.ui["danger"])
        elif state_text == "running":
            self.timer_card.configure(highlightbackground=self.ui["accent"])
        else:
            self.timer_card.configure(highlightbackground=self.ui["line"])

    def _new_session_csv_path(self) -> Path:
        ts = self._timestamp_string()
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
        return parent / f"{stem}_{ts}_{self.tracker.session_id if hasattr(self, 'tracker') else 'session'}{suffix}"

    @staticmethod
    def _timestamp_string() -> str:
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _rotate_session_csv(self) -> None:
        self.current_csv_path = self._new_session_csv_path()
        self.tracker.logger = CsvRunLogger(self.current_csv_path)
        self.csv_var.set(f"CSV {self.current_csv_path.name}")
        self.log.info("session_csv_rotated path=%s", self.current_csv_path)

    def _open_settings_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        frame = tk.Frame(win)
        frame.pack(padx=12, pady=12)
        try:
            win.configure(bg=self.ui["bg_app"])
            frame.configure(bg=self.ui["bg_card"])
        except Exception:
            pass

        enabled_var = tk.BooleanVar(value=self.controller_config.enabled)
        device_var = tk.StringVar(value=str(self.controller_config.device_index))
        hat_var = tk.StringVar(value=str(self.controller_config.hat_index))
        guard_var = tk.StringVar(value=str(self.controller_config.repeat_guard_ms))
        dpad_direction_choices = ["none", "up", "right", "down", "left"]
        inverse_dpad = {action: "none" for action in ACTION_ORDER}
        for direction, action in self.controller_config.dpad_map.items():
            if action in inverse_dpad:
                inverse_dpad[action] = direction
        keyboard_vars = {action: tk.StringVar(value=self.controller_config.keyboard_map.get(action, "")) for action in ACTION_ORDER}
        dpad_for_action_vars = {action: tk.StringVar(value=inverse_dpad.get(action, "none")) for action in ACTION_ORDER}
        capture_state = {"action": None}
        capture_info_var = tk.StringVar(value="Keyboard: click Record, then press a key combo.")

        local_pressed_mods: set[str] = set()

        def _keysym_to_mod(keysym: str) -> str | None:
            k = (keysym or "").lower()
            if k in {"meta_l", "meta_r", "command"}:
                return "cmd"
            if k in {"alt_l", "alt_r", "option_l", "option_r"}:
                return "alt"
            if k in {"control_l", "control_r"}:
                return "ctrl"
            if k in {"shift_l", "shift_r"}:
                return "shift"
            return None

        def _keysym_to_token(keysym: str) -> str | None:
            k = (keysym or "").lower()
            special = {
                "exclamdown": "1",
                "trademark": "2",
                "sterling": "3",
                "cent": "4",
                "infinity": "5",
            }
            if k in special:
                return special[k]
            if len(k) == 1:
                return k
            if k.startswith("f") and k[1:].isdigit():
                return k
            named = {"space": "space", "return": "enter", "escape": "esc", "tab": "tab"}
            return named.get(k)

        def _on_settings_keypress(event: tk.Event) -> None:
            mod = _keysym_to_mod(getattr(event, "keysym", ""))
            if mod:
                local_pressed_mods.add(mod)
                return
            action = capture_state["action"]
            if not action:
                return
            token = _keysym_to_token(getattr(event, "keysym", ""))
            if not token:
                capture_info_var.set(f"Unsupported key: {getattr(event, 'keysym', '?')}")
                return
            combo = "+".join([*sorted(local_pressed_mods, key=lambda m: ["cmd", "ctrl", "alt", "shift"].index(m) if m in ["cmd", "ctrl", "alt", "shift"] else 99), token])
            combo = normalize_combo_string(combo)
            keyboard_vars[action].set(combo)
            capture_info_var.set(f"Recorded {ACTION_TITLES[action]} = {combo}")
            capture_state["action"] = None

        def _on_settings_keyrelease(event: tk.Event) -> None:
            mod = _keysym_to_mod(getattr(event, "keysym", ""))
            if mod:
                local_pressed_mods.discard(mod)

        win.bind("<KeyPress>", _on_settings_keypress, add="+")
        win.bind("<KeyRelease>", _on_settings_keyrelease, add="+")

        row = 0
        tk.Checkbutton(
            frame,
            text="Enable controller",
            variable=enabled_var,
            bg=self.ui["bg_card"],
            fg=self.ui["fg_primary"],
            selectcolor=self.ui["bg_card"],
            activebackground=self.ui["bg_card"],
            font=self.ui["font_small"],
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        tk.Label(frame, text="Device index", bg=self.ui["bg_card"], fg=self.ui["fg_primary"], font=self.ui["font_small"]).grid(row=row, column=0, sticky="w", pady=(6, 2))
        tk.Entry(frame, textvariable=device_var, width=8, font=self.ui["font_small"]).grid(row=row, column=1, sticky="w", pady=(6, 2))
        row += 1
        tk.Label(frame, text="Hat index (D-pad)", bg=self.ui["bg_card"], fg=self.ui["fg_primary"], font=self.ui["font_small"]).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(frame, textvariable=hat_var, width=8, font=self.ui["font_small"]).grid(row=row, column=1, sticky="w", pady=2)
        row += 1
        tk.Label(frame, text="Repeat guard ms", bg=self.ui["bg_card"], fg=self.ui["fg_primary"], font=self.ui["font_small"]).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(frame, textvariable=guard_var, width=8, font=self.ui["font_small"]).grid(row=row, column=1, sticky="w", pady=2)
        row += 1

        tk.Label(frame, text="Action", font=(self.ui["font_ui"][0], self.ui["font_ui"][1], "bold"), bg=self.ui["bg_card"], fg=self.ui["fg_primary"]).grid(row=row, column=0, sticky="w", pady=(8, 2))
        tk.Label(frame, text="Keyboard", font=(self.ui["font_ui"][0], self.ui["font_ui"][1], "bold"), bg=self.ui["bg_card"], fg=self.ui["fg_primary"]).grid(row=row, column=1, sticky="w", pady=(8, 2))
        tk.Label(frame, text="D-pad", font=(self.ui["font_ui"][0], self.ui["font_ui"][1], "bold"), bg=self.ui["bg_card"], fg=self.ui["fg_primary"]).grid(row=row, column=2, sticky="w", pady=(8, 2))
        row += 1

        for action in ACTION_ORDER:
            tk.Label(frame, text=ACTION_TITLES[action], bg=self.ui["bg_card"], fg=self.ui["fg_primary"], font=self.ui["font_small"]).grid(row=row, column=0, sticky="w", pady=2)

            k_frame = tk.Frame(frame)
            k_frame.grid(row=row, column=1, sticky="w", pady=2)
            tk.Entry(k_frame, textvariable=keyboard_vars[action], width=18, font=self.ui["font_small"]).pack(side="left")

            def _make_record_callback(action_name: str):
                def _record() -> None:
                    capture_state["action"] = action_name
                    capture_info_var.set(f"Recording for {ACTION_TITLES[action_name]}: press key combo...")
                    win.focus_force()
                return _record

            tk.Button(k_frame, text="Record", command=_make_record_callback(action)).pack(side="left", padx=(4, 0))
            tk.Button(k_frame, text="Clear", command=lambda a=action: keyboard_vars[a].set("")).pack(side="left", padx=(4, 0))

            ttk.Combobox(
                frame,
                textvariable=dpad_for_action_vars[action],
                values=dpad_direction_choices,
                width=10,
                state="readonly",
            ).grid(row=row, column=2, sticky="w", pady=2)
            row += 1

        info = tk.Label(
            frame,
            textvariable=capture_info_var,
            justify="left",
            fg=self.ui["fg_muted"],
            bg=self.ui["bg_card"],
            font=self.ui["font_small"],
        )
        info.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 4))
        row += 1

        def _save_and_close() -> None:
            try:
                keyboard_map = {a: normalize_combo_string(v.get()) for a, v in keyboard_vars.items()}
                duplicate_keyboard = self._find_duplicate_bindings(keyboard_map)
                if duplicate_keyboard:
                    combo, actions = duplicate_keyboard
                    raise ValueError(f"Duplicate keyboard binding '{combo}' for: {', '.join(actions)}")

                selected_dirs = {a: dpad_for_action_vars[a].get() for a in ACTION_ORDER}
                duplicate_dpad = self._find_duplicate_dpad(selected_dirs)
                if duplicate_dpad:
                    direction, actions = duplicate_dpad
                    raise ValueError(f"Duplicate D-pad direction '{direction}' for: {', '.join(actions)}")

                dpad_map = {"up": "none", "right": "none", "down": "none", "left": "none"}
                for action_name, direction in selected_dirs.items():
                    if direction in dpad_map:
                        dpad_map[direction] = action_name

                new_cfg = ControllerConfig(
                    enabled=enabled_var.get(),
                    device_index=int(device_var.get().strip() or "0"),
                    hat_index=int(hat_var.get().strip() or "0"),
                    repeat_guard_ms=int(guard_var.get().strip() or "150"),
                    dpad_map=dpad_map,
                    keyboard_map=keyboard_map,
                )
                save_controller_config(self.controller_config_path, new_cfg, self.log)
                self.controller_config = load_controller_config(self.controller_config_path, self.log)
                self.hotkeys.reload_bindings(self.controller_config.keyboard_map)
                self.controller.reload(self.controller_config)
                self._bind_local_hotkeys()
                self._refresh_action_button_labels()
                self.status_var.set("Controller settings saved and reloaded.")
                self.log.info("controller_settings_reloaded")
                win.destroy()
            except Exception as exc:
                self.log.exception("controller_settings_save_failed")
                self.status_var.set(f"Settings save failed: {exc}")

        buttons = tk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(6, 0))
        tk.Button(buttons, text="Cancel", command=win.destroy).pack(side="left")
        tk.Button(buttons, text="Save", command=_save_and_close).pack(side="left", padx=(6, 0))

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

    def _log_local_keypress(self, event: tk.Event) -> None:
        self.log.info(
            "tk_key_press keysym=%s keycode=%s state=0x%x widget=%s",
            getattr(event, "keysym", None),
            getattr(event, "keycode", None),
            getattr(event, "state", 0),
            getattr(event.widget, "winfo_class", lambda: "?")(),
        )

    def _log_local_keyrelease(self, event: tk.Event) -> None:
        self.log.info(
            "tk_key_release keysym=%s keycode=%s state=0x%x widget=%s",
            getattr(event, "keysym", None),
            getattr(event, "keycode", None),
            getattr(event, "state", 0),
            getattr(event.widget, "winfo_class", lambda: "?")(),
        )

    def _limit_text(self) -> str:
        return f"Saved runs in session: {self.tracker.saved_runs_count}/{self.SESSION_RUN_LIMIT}"

    def _run_limit_reached(self) -> bool:
        return self.tracker.saved_runs_count >= self.SESSION_RUN_LIMIT

    def _is_blocked_by_run_limit(self, action: str) -> bool:
        if not self._run_limit_reached():
            return False
        return action in {"toggle_start_stop", "next_run", "reset_timer", "undo_last"}

    def _run_limit_message(self) -> str:
        return f"Run limit {self.SESSION_RUN_LIMIT} reached. Create a new session."

    def _update_control_states(self) -> None:
        blocked = self._run_limit_reached()
        disabled = tk.DISABLED if blocked else tk.NORMAL
        self.btn_start_stop.configure(state=disabled)
        self.btn_next_run.configure(state=disabled)
        self.btn_reset_timer.configure(state=disabled)
        self.btn_undo.configure(state=disabled)
        self.note_entry.configure(state=disabled)
        self.btn_reset_session.configure(state=tk.NORMAL)
        self.btn_settings.configure(state=tk.NORMAL)
        self.limit_var.set(self._limit_text())
        self._refresh_visual_state()

    def handle_action(self, action: str, source: str = "unknown") -> None:
        self.log.info("action_received source=%s action=%s", source, action)
        if self._is_blocked_by_run_limit(action):
            self.status_var.set(self._run_limit_message())
            self._update_control_states()
            self.log.warning(
                "action_blocked_by_run_limit source=%s action=%s saved_runs=%s limit=%s",
                source,
                action,
                self.tracker.saved_runs_count,
                self.SESSION_RUN_LIMIT,
            )
            self._refresh_visual_state()
            return
        if action == "toggle_start_stop":
            result = self.tracker.toggle_start_stop()
            self.state_var.set("Running" if self.tracker.is_running else "Paused")
            self.status_var.set("Timer started" if result == "started" else "Timer stopped")

        elif action == "next_run":
            note = self.note_var.get()
            record, result = self.tracker.next_run(note=note, max_saved_runs=self.SESSION_RUN_LIMIT)
            self.state_var.set("Running" if self.tracker.is_running else "Idle")
            if result == "started_first_run":
                self.status_var.set("Started run #1 (no CSV row yet).")
            elif result == "saved_limit_reached":
                self.note_var.set("")
                self.status_var.set(
                    f"Saved run #{record.run_number}: {record.duration_sec:.3f}s. {self._run_limit_message()}"
                )
            else:
                self.note_var.set("")
                self.status_var.set(
                    f"Saved run #{record.run_number}: {record.duration_sec:.3f}s; started run #{self.tracker.run_number}."
                )

        elif action == "reset_timer":
            self.tracker.reset_timer()
            self.state_var.set("Idle")
            self.status_var.set("Current timer reset (run number unchanged).")

        elif action == "reset_session":
            self._rotate_session_csv()
            self.tracker.reset_session()
            self.note_var.set("")
            self.state_var.set("Idle")
            self.status_var.set(f"New session started; CSV {self.current_csv_path.name}; counter reset to run #1.")

        elif action == "undo_last":
            undone, reason = self.tracker.undo_last_run()
            if undone:
                self.status_var.set("Removed last CSV row for current session.")
            elif reason == "active_run_present":
                if sys.platform == "darwin":
                    self.status_var.set("Undo blocked: reset current run first (cmd+alt+3), then cmd+alt+5.")
                else:
                    self.status_var.set("Undo blocked: reset current run first (Ctrl+Alt+3), then Ctrl+Alt+5.")
            else:
                self.status_var.set("Nothing to undo for this session.")

        self._refresh_labels()
        self._update_control_states()
        self.log.info(
            "action_applied source=%s action=%s run=%s session=%s running=%s elapsed_ms=%s",
            source,
            action,
            self.tracker.run_number,
            self.tracker.session_id,
            self.tracker.is_running,
            self.tracker.current_elapsed_ms(),
        )
        self._refresh_visual_state()

    def _refresh_labels(self) -> None:
        self.run_var.set(f"Run #{self.tracker.run_number}")
        self.session_var.set(f"Session {self.tracker.session_id}")
        self.csv_var.set(f"CSV {self.current_csv_path.name}")
        self.limit_var.set(self._limit_text())
        self.timer_var.set(self.tracker.formatted_elapsed())
        if self.tracker.is_running:
            self.state_var.set("Running")

    def _drain_hotkey_queue(self) -> None:
        while True:
            try:
                source, action = self.command_queue.get_nowait()
            except Empty:
                break
            self.handle_action(action, source=source)

    def _tick(self) -> None:
        self._drain_hotkey_queue()
        self.timer_var.set(self.tracker.formatted_elapsed())
        self.root.after(100, self._tick)

    def run(self) -> None:
        self.hotkeys.start()
        self.controller.start()
        self.status_var.set(f"Ready. CSV: {self.current_csv_path.name}")
        if self.hotkeys.available:
            self.status_var.set(f"Global hotkeys active. CSV: {self.current_csv_path.name}")
        elif self.hotkeys.error:
            self.status_var.set(f"{self.hotkeys.error} (window hotkeys still work)")
            self.log.warning("global_hotkeys_unavailable error=%s", self.hotkeys.error)
        else:
            self.log.warning("global_hotkeys_unavailable error=unknown")
        if self.controller.error:
            self.log.warning("controller_unavailable error=%s", self.controller.error)
        self._update_control_states()
        self._refresh_visual_state()
        self._tick()
        self.root.mainloop()
