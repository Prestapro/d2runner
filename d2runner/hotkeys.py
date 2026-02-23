from __future__ import annotations

import logging
from typing import Callable
import sys
import time


HotkeyHandler = Callable[[str], None]
ParsedCombo = tuple[frozenset[str], str]

ACTION_ORDER = (
    "toggle_start_stop",
    "next_run",
    "reset_timer",
    "reset_session",
    "undo_last",
)

ACTION_TITLES = {
    "toggle_start_stop": "Start/Stop",
    "next_run": "Next Run",
    "reset_timer": "Reset Timer",
    "reset_session": "New Session",
    "undo_last": "Undo",
}


def _symbol_to_digit(ch: str) -> str | None:
    return {
        "¡": "1",
        "™": "2",
        "£": "3",
        "¢": "4",
        "∞": "5",
    }.get(ch)


def normalize_combo_string(combo: str) -> str:
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return ""
    mods: list[str] = []
    key = ""
    for part in parts:
        p = {"command": "cmd", "option": "alt", "control": "ctrl"}.get(part, part)
        if p in {"cmd", "alt", "ctrl", "shift"}:
            if p not in mods:
                mods.append(p)
        else:
            key = p
    if not key:
        return "+".join(mods)
    return "+".join([*mods, key])


def parse_combo_string(combo: str) -> ParsedCombo | None:
    norm = normalize_combo_string(combo)
    if not norm:
        return None
    parts = norm.split("+")
    if not parts:
        return None
    mods = [p for p in parts[:-1] if p]
    key = parts[-1]
    if not key:
        return None
    return frozenset(mods), key


def human_combo_label(combo: str) -> str:
    norm = normalize_combo_string(combo)
    if not norm:
        return ""
    parts = norm.split("+")
    out: list[str] = []
    for p in parts:
        if sys.platform == "darwin":
            out.append({"cmd": "⌘", "alt": "⌥", "ctrl": "⌃", "shift": "⇧"}.get(p, p.upper()))
        else:
            out.append({"cmd": "Win", "alt": "Alt", "ctrl": "Ctrl", "shift": "Shift"}.get(p, p.upper()))
    return "".join(out) if sys.platform == "darwin" else "+".join(out)


def _apply_pynput_macos_globalhotkeys_compat(keyboard_module: object) -> None:
    # pynput 1.8.1 on macOS can call GlobalHotKeys callbacks with only `key`,
    # while the methods require `(key, injected)`, causing a listener crash.
    if sys.platform != "darwin":
        return

    GlobalHotKeys = getattr(keyboard_module, "GlobalHotKeys", None)
    if GlobalHotKeys is None:
        return

    if getattr(GlobalHotKeys, "_d2runner_compat_patched", False):
        return

    def _on_press(self, key, injected=False):  # type: ignore[no-redef]
        if not injected:
            for hotkey in self._hotkeys:
                hotkey.press(self.canonical(key))

    def _on_release(self, key, injected=False):  # type: ignore[no-redef]
        if not injected:
            for hotkey in self._hotkeys:
                hotkey.release(self.canonical(key))

    GlobalHotKeys._on_press = _on_press
    GlobalHotKeys._on_release = _on_release
    GlobalHotKeys._d2runner_compat_patched = True


class HotkeyBackend:
    def __init__(self, on_action: HotkeyHandler, keyboard_map: dict[str, str], enabled: bool = True) -> None:
        self.on_action = on_action
        self._listener = None
        self.available = False
        self.error: str | None = None
        self.log = logging.getLogger("d2runner.hotkeys")
        self._pressed_mods: set[str] = set()
        self._fired_keys: set[str] = set()
        self._last_action_at: dict[str, float] = {}
        self._repeat_guard_ms = 700
        self.enabled = bool(enabled)
        self.keyboard_map = dict(keyboard_map)
        self._parsed_bindings: dict[str, ParsedCombo] = {}
        self._reload_parsed_bindings()

    def start(self) -> None:
        if not self.enabled:
            self.available = False
            self.error = "keyboard hotkeys disabled in config"
            self.log.info("hotkeys_disabled")
            return
        try:
            from pynput import keyboard  # type: ignore
        except Exception as exc:  # pragma: no cover
            self.error = f"pynput unavailable: {exc}"
            self.available = False
            return

        _apply_pynput_macos_globalhotkeys_compat(keyboard)
        self.log.info("hotkeys_backend_start platform=%s", sys.platform)
        self.log.info("hotkeys_bindings %s", self.keyboard_map)

        try:
            self._listener = keyboard.Listener(
                on_press=self._make_on_press(keyboard),
                on_release=self._make_on_release(keyboard),
            )
            self._listener.start()
            self.available = True
        except Exception as exc:  # pragma: no cover
            self.error = f"global hotkeys unavailable: {exc}"
            self.log.exception("hotkeys_backend_failed")
            self.available = False

    def reload_bindings(self, keyboard_map: dict[str, str], enabled: bool | None = None) -> None:
        if enabled is not None:
            self.enabled = bool(enabled)
        self.keyboard_map = dict(keyboard_map)
        self._reload_parsed_bindings()
        self.log.info("hotkeys_bindings_reloaded enabled=%s %s", self.enabled, self.keyboard_map)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _reload_parsed_bindings(self) -> None:
        parsed: dict[str, ParsedCombo] = {}
        for action in ACTION_ORDER:
            combo = self.keyboard_map.get(action, "")
            p = parse_combo_string(combo)
            if p is None:
                self.log.warning("hotkey_binding_invalid action=%s combo=%r", action, combo)
                continue
            parsed[action] = p
        self._parsed_bindings = parsed

    def _modifier_name(self, key: object, keyboard_module: object) -> str | None:
        Key = getattr(keyboard_module, "Key")
        if key in {Key.cmd, Key.cmd_l, Key.cmd_r}:
            return "cmd"
        if key in {Key.alt, Key.alt_l, Key.alt_r, getattr(Key, "alt_gr", None)}:
            return "alt"
        if key in {Key.ctrl, Key.ctrl_l, Key.ctrl_r}:
            return "ctrl"
        if key in {Key.shift, Key.shift_l, Key.shift_r}:
            return "shift"
        return None

    def _digit_name(self, key: object, keyboard_module: object) -> str | None:
        KeyCode = getattr(keyboard_module, "KeyCode")
        if isinstance(key, KeyCode):
            ch = getattr(key, "char", None)
            if ch in {"1", "2", "3", "4", "5"}:
                return ch
            mapped = _symbol_to_digit(ch)
            if mapped is not None:
                self.log.info("digit_normalized raw_char=%r digit=%s", ch, mapped)
                return mapped
        return None

    def _key_token(self, key: object, keyboard_module: object) -> str | None:
        digit = self._digit_name(key, keyboard_module)
        if digit:
            return digit

        Key = getattr(keyboard_module, "Key")
        KeyCode = getattr(keyboard_module, "KeyCode")
        if isinstance(key, KeyCode):
            ch = getattr(key, "char", None)
            if ch:
                return str(ch).lower()
            return None

        key_name = getattr(key, "name", None)
        if key_name:
            return str(key_name).lower()
        try:
            for i in range(1, 25):
                if key == getattr(Key, f"f{i}", None):
                    return f"f{i}"
        except Exception:
            pass
        return None

    def _make_on_press(self, keyboard_module: object):
        def _on_press(key: object) -> None:
            mod = self._modifier_name(key, keyboard_module)
            if mod:
                self._pressed_mods.add(mod)
                self.log.info("raw_press key=%s mods=%s", mod, sorted(self._pressed_mods))
                return

            digit = self._digit_name(key, keyboard_module)
            token = digit or self._key_token(key, keyboard_module)
            if token:
                self.log.info("raw_press key=%s mods=%s", token, sorted(self._pressed_mods))
                if token in self._fired_keys:
                    return
                for action, (req_mods, req_key) in self._parsed_bindings.items():
                    if req_key == token and req_mods.issubset(self._pressed_mods):
                        self.log.info(
                            "hotkey_matched key=%s mods=%s action=%s",
                            token,
                            sorted(self._pressed_mods),
                            action,
                        )
                        if self._should_throttle_action(action):
                            self.log.info(
                                "hotkey_throttled key=%s mods=%s action=%s repeat_guard_ms=%s",
                                token,
                                sorted(self._pressed_mods),
                                action,
                                self._repeat_guard_ms,
                            )
                            self._fired_keys.add(token)
                            return
                        self._fired_keys.add(token)
                        self.on_action(action)
                        return
                return

            self.log.info("raw_press key=%r mods=%s", key, sorted(self._pressed_mods))

        return _on_press

    def _make_on_release(self, keyboard_module: object):
        def _on_release(key: object) -> None:
            mod = self._modifier_name(key, keyboard_module)
            if mod:
                self._pressed_mods.discard(mod)
                if not self._pressed_mods:
                    self._fired_keys.clear()
                self.log.info("raw_release key=%s mods=%s", mod, sorted(self._pressed_mods))
                return

            token = self._key_token(key, keyboard_module)
            if token:
                self._fired_keys.discard(token)
                self.log.info("raw_release key=%s mods=%s", token, sorted(self._pressed_mods))
                return

            self.log.info("raw_release key=%r mods=%s", key, sorted(self._pressed_mods))

        return _on_release

    def _should_throttle_action(self, action: str) -> bool:
        now = time.monotonic()
        last = self._last_action_at.get(action)
        self._last_action_at[action] = now
        if last is None:
            return False
        return (now - last) * 1000 < self._repeat_guard_ms
