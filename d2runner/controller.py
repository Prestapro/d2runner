from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import os
import sys
import threading
import time
import ctypes


VALID_ACTIONS = {
    "toggle_start_stop",
    "next_run",
    "reset_timer",
    "reset_session",
    "undo_last",
    "none",
}


@dataclass
class ControllerConfig:
    enabled: bool
    device_index: int
    hat_index: int
    dpad_map: dict[str, str]
    keyboard_map: dict[str, str]
    repeat_guard_ms: int = 150


def default_controller_config_data() -> dict[str, object]:
    if sys.platform == "darwin":
        keyboard_map = {
            "toggle_start_stop": "cmd+alt+1",
            "next_run": "cmd+alt+2",
            "reset_timer": "cmd+alt+3",
            "reset_session": "cmd+alt+4",
            "undo_last": "cmd+alt+5",
        }
    else:
        keyboard_map = {
            "toggle_start_stop": "ctrl+alt+1",
            "next_run": "ctrl+alt+2",
            "reset_timer": "ctrl+alt+3",
            "reset_session": "ctrl+alt+4",
            "undo_last": "ctrl+alt+5",
        }
    return {
        "enabled": True,
        "device_index": 0,
        "hat_index": 0,
        "repeat_guard_ms": 150,
        "keyboard_map": keyboard_map,
        "dpad_map": {
            "up": "toggle_start_stop",
            "right": "next_run",
            "down": "reset_timer",
            "left": "reset_session",
        },
    }


def load_controller_config(config_path: Path, log: logging.Logger | None = None) -> ControllerConfig:
    config_path = Path(config_path)
    logger = log or logging.getLogger("d2runner.controller")

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(default_controller_config_data(), indent=2), encoding="utf-8")
        logger.info("controller_config_created path=%s", config_path)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    keyboard_map_raw = raw.get("keyboard_map", {})
    keyboard_map: dict[str, str] = {}
    for action in ("toggle_start_stop", "next_run", "reset_timer", "reset_session", "undo_last"):
        keyboard_map[action] = str(keyboard_map_raw.get(action, default_controller_config_data()["keyboard_map"][action]))

    dpad_map_raw = raw.get("dpad_map", {})
    dpad_map: dict[str, str] = {}
    for direction in ("up", "right", "down", "left"):
        action = str(dpad_map_raw.get(direction, "none"))
        if action not in VALID_ACTIONS:
            logger.warning(
                "controller_config_invalid_action direction=%s action=%s fallback=none path=%s",
                direction,
                action,
                config_path,
            )
            action = "none"
        dpad_map[direction] = action

    cfg = ControllerConfig(
        enabled=bool(raw.get("enabled", True)),
        device_index=int(raw.get("device_index", 0)),
        hat_index=int(raw.get("hat_index", 0)),
        repeat_guard_ms=int(raw.get("repeat_guard_ms", 150)),
        dpad_map=dpad_map,
        keyboard_map=keyboard_map,
    )
    logger.info(
        "controller_config_loaded path=%s enabled=%s device_index=%s hat_index=%s keyboard_map=%s dpad_map=%s",
        config_path,
        cfg.enabled,
        cfg.device_index,
        cfg.hat_index,
        cfg.keyboard_map,
        cfg.dpad_map,
    )
    return cfg


def save_controller_config(config_path: Path, config: ControllerConfig, log: logging.Logger | None = None) -> None:
    config_path = Path(config_path)
    logger = log or logging.getLogger("d2runner.controller")
    payload = {
        "enabled": config.enabled,
        "device_index": config.device_index,
        "hat_index": config.hat_index,
        "repeat_guard_ms": config.repeat_guard_ms,
        "keyboard_map": {
            "toggle_start_stop": config.keyboard_map.get("toggle_start_stop", ""),
            "next_run": config.keyboard_map.get("next_run", ""),
            "reset_timer": config.keyboard_map.get("reset_timer", ""),
            "reset_session": config.keyboard_map.get("reset_session", ""),
            "undo_last": config.keyboard_map.get("undo_last", ""),
        },
        "dpad_map": {
            "up": config.dpad_map.get("up", "none"),
            "right": config.dpad_map.get("right", "none"),
            "down": config.dpad_map.get("down", "none"),
            "left": config.dpad_map.get("left", "none"),
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "controller_config_saved path=%s keyboard_map=%s dpad_map=%s",
        config_path,
        payload["keyboard_map"],
        payload["dpad_map"],
    )


class ControllerBackend:
    def __init__(self, on_action, config: ControllerConfig) -> None:
        self.on_action = on_action
        self.config = config
        self.available = False
        self.error: str | None = None
        self.log = logging.getLogger("d2runner.controller")
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_fired_at: dict[str, float] = {}

    def start(self) -> None:
        if not self.config.enabled:
            self.error = "controller disabled in config"
            self.log.info("controller_disabled")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="d2runner-controller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def reload(self, config: ControllerConfig) -> None:
        self.log.info("controller_reload_requested")
        self.stop()
        self.config = config
        self.error = None
        self.start()

    def _run(self) -> None:
        pygame = None
        try:
            # On Windows/Linux we run the controller backend in a background thread
            # without a pygame window. Joystick state updates still rely on SDL's
            # event subsystem, so use the dummy video driver before importing pygame.
            if sys.platform != "darwin":
                os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

            import pygame as _pygame  # type: ignore

            pygame = _pygame
            # Do not call pygame.init() in this background thread on macOS:
            # it initializes SDL video/keyboard and can crash with dispatch
            # assertions inside HIToolbox when not on the main thread.
            if sys.platform != "darwin":
                try:
                    pygame.display.init()
                except Exception as exc:
                    self.log.warning("controller_display_init_failed %s", exc)
            pygame.joystick.init()

            count = pygame.joystick.get_count()
            self.log.info("controller_devices_detected count=%s", count)
            if count <= self.config.device_index:
                self.error = f"controller not found at index {self.config.device_index} (count={count})"
                self.log.warning("controller_not_found %s", self.error)
                return

            joystick = pygame.joystick.Joystick(self.config.device_index)
            joystick.init()
            self.available = True
            self.log.info(
                "controller_connected name=%s index=%s instance_id=%s hats=%s buttons=%s axes=%s",
                joystick.get_name(),
                self.config.device_index,
                getattr(joystick, "get_instance_id", lambda: "n/a")(),
                joystick.get_numhats(),
                joystick.get_numbuttons(),
                joystick.get_numaxes(),
            )
            has_hat = joystick.get_numhats() > self.config.hat_index
            button_dpad_map = self._guess_dpad_button_map(joystick.get_numbuttons())
            axis_dpad_pair = self._guess_dpad_axis_pair(joystick.get_numaxes())
            if not has_hat and button_dpad_map is None and axis_dpad_pair is None:
                self.error = (
                    f"controller dpad not available (hats={joystick.get_numhats()}, "
                    f"buttons={joystick.get_numbuttons()}, axes={joystick.get_numaxes()})"
                )
                self.log.warning("controller_dpad_not_available %s", self.error)
                return

            self.log.info(
                "controller_dpad_sources hat=%s hat_index=%s button_map=%s axis_pair=%s",
                has_hat,
                self.config.hat_index,
                button_dpad_map,
                axis_dpad_pair,
            )

            xinput = self._try_init_xinput(self.config.device_index)
            if xinput is not None:
                self.log.info(
                    "controller_xinput_enabled user_index=%s dll=%s",
                    xinput["user_index"],
                    xinput["dll_name"],
                )

            prev_hat_value: tuple[int, int] | None = None
            prev_axes_direction: str | None = None
            prev_buttons_pressed: set[int] = set()
            prev_all_buttons_pressed: set[int] = set()
            pump_failed_logged = False
            prev_xinput_buttons: int | None = None

            while not self._stop.is_set():
                direction: str | None = None

                # SDL-backed joystick state is refreshed by pumping the event queue.
                # Without this, hat/button/axis values may remain stuck at startup.
                try:
                    pygame.event.pump()
                except Exception as exc:
                    if not pump_failed_logged:
                        pump_failed_logged = True
                        self.log.warning("controller_event_pump_failed %s", exc)

                if xinput is not None:
                    xinput_direction, prev_xinput_buttons = self._poll_xinput_dpad(
                        xinput,
                        prev_xinput_buttons,
                    )
                    if xinput_direction is not None:
                        direction = xinput_direction

                if direction is None and has_hat:
                    try:
                        value = joystick.get_hat(self.config.hat_index)
                    except Exception as exc:
                        self.error = f"controller hat polling failed: {exc}"
                        self.log.exception("controller_hat_poll_failed")
                        return
                    if value != prev_hat_value:
                        prev_hat_value = value
                        self.log.info("controller_hat_motion hat=%s value=%s", self.config.hat_index, value)
                        direction = self._direction_from_hat(value)

                if direction is None and axis_dpad_pair is not None:
                    axes_direction = self._poll_axes_dpad(joystick, axis_dpad_pair)
                    if axes_direction != prev_axes_direction:
                        self.log.info("controller_axes_dpad axis_pair=%s direction=%s", axis_dpad_pair, axes_direction)
                        prev_axes_direction = axes_direction
                        direction = axes_direction

                if direction is None and button_dpad_map is not None:
                    prev_all_buttons_pressed = self._poll_all_buttons_debug(
                        joystick,
                        prev_all_buttons_pressed,
                    )
                    buttons_direction, prev_buttons_pressed = self._poll_buttons_dpad(
                        joystick,
                        button_dpad_map,
                        prev_buttons_pressed,
                    )
                    direction = buttons_direction

                if direction is not None:
                    action = self.config.dpad_map.get(direction, "none")
                    if action == "none":
                        self.log.info("controller_direction_ignored direction=%s", direction)
                    elif self._should_throttle(direction):
                        self.log.info("controller_direction_throttled direction=%s action=%s", direction, action)
                    else:
                        self.log.info("controller_action direction=%s action=%s", direction, action)
                        self.on_action(action)
                time.sleep(0.01)
        except Exception as exc:  # pragma: no cover
            self.error = f"controller backend failed: {exc}"
            self.log.exception("controller_backend_failed")
        finally:
            self.available = False
            if pygame is not None:
                try:
                    pygame.joystick.quit()
                except Exception:
                    pass

    @staticmethod
    def _direction_from_hat(value: object) -> str | None:
        if not isinstance(value, tuple) or len(value) != 2:
            return None
        x, y = value
        if (x, y) == (0, 1):
            return "up"
        if (x, y) == (1, 0):
            return "right"
        if (x, y) == (0, -1):
            return "down"
        if (x, y) == (-1, 0):
            return "left"
        return None

    @staticmethod
    def _guess_dpad_button_map(num_buttons: int) -> dict[int, str] | None:
        # Common Xbox/XInput layouts in SDL/pygame joystick mode map D-pad to buttons 11..14.
        if num_buttons >= 15:
            return {11: "up", 12: "down", 13: "left", 14: "right"}
        return None

    @staticmethod
    def _guess_dpad_axis_pair(num_axes: int) -> tuple[int, int] | None:
        # Some Windows drivers expose D-pad as discrete axes 6/7 (-1,0,1).
        if num_axes >= 8:
            return (6, 7)
        return None

    def _poll_axes_dpad(self, joystick, axis_pair: tuple[int, int]) -> str | None:
        ax_x, ax_y = axis_pair
        try:
            x = float(joystick.get_axis(ax_x))
            y = float(joystick.get_axis(ax_y))
        except Exception:
            return None
        threshold = 0.5
        qx = -1 if x < -threshold else (1 if x > threshold else 0)
        qy = -1 if y < -threshold else (1 if y > threshold else 0)
        if (qx, qy) == (0, 0):
            return None
        if (qx, qy) == (0, -1):
            return "up"
        if (qx, qy) == (1, 0):
            return "right"
        if (qx, qy) == (0, 1):
            return "down"
        if (qx, qy) == (-1, 0):
            return "left"
        return None

    def _poll_buttons_dpad(
        self,
        joystick,
        button_map: dict[int, str],
        prev_pressed: set[int],
    ) -> tuple[str | None, set[int]]:
        pressed_now: set[int] = set()
        for btn_idx in button_map:
            try:
                if joystick.get_button(btn_idx):
                    pressed_now.add(btn_idx)
            except Exception:
                continue

        if pressed_now != prev_pressed:
            self.log.info(
                "controller_button_dpad pressed=%s released=%s raw_pressed=%s map=%s",
                sorted(pressed_now - prev_pressed),
                sorted(prev_pressed - pressed_now),
                sorted(pressed_now),
                button_map,
            )

        newly_pressed = [i for i in sorted(pressed_now) if i not in prev_pressed]
        direction = button_map[newly_pressed[0]] if newly_pressed else None
        return direction, pressed_now

    def _poll_all_buttons_debug(self, joystick, prev_pressed: set[int]) -> set[int]:
        try:
            count = int(joystick.get_numbuttons())
        except Exception:
            return prev_pressed
        pressed_now: set[int] = set()
        for idx in range(count):
            try:
                if joystick.get_button(idx):
                    pressed_now.add(idx)
            except Exception:
                continue
        if pressed_now != prev_pressed:
            self.log.info(
                "controller_button_debug pressed=%s released=%s raw_pressed=%s",
                sorted(pressed_now - prev_pressed),
                sorted(prev_pressed - pressed_now),
                sorted(pressed_now),
            )
        return pressed_now

    def _should_throttle(self, direction: str) -> bool:
        now = time.monotonic()
        last = self._last_fired_at.get(direction)
        self._last_fired_at[direction] = now
        if last is None:
            return False
        return (now - last) * 1000 < max(self.config.repeat_guard_ms, 0)

    def _try_init_xinput(self, user_index: int) -> dict[str, object] | None:
        if sys.platform != "win32":
            return None
        if user_index < 0 or user_index > 3:
            return None
        for dll_name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
            try:
                dll = ctypes.WinDLL(dll_name)
            except Exception:
                continue
            try:
                get_state = dll.XInputGetState
            except Exception:
                continue
            get_state.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            get_state.restype = ctypes.c_uint32
            xstate = self._xinput_state_struct()
            state = xstate()
            rc = int(get_state(int(user_index), ctypes.byref(state)))
            if rc == 0:
                return {"dll": dll, "dll_name": dll_name, "get_state": get_state, "user_index": int(user_index)}
        return None

    @staticmethod
    def _xinput_state_struct():
        class XINPUT_GAMEPAD(ctypes.Structure):
            _fields_ = [
                ("wButtons", ctypes.c_uint16),
                ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_int16),
                ("sThumbLY", ctypes.c_int16),
                ("sThumbRX", ctypes.c_int16),
                ("sThumbRY", ctypes.c_int16),
            ]

        class XINPUT_STATE(ctypes.Structure):
            _fields_ = [
                ("dwPacketNumber", ctypes.c_uint32),
                ("Gamepad", XINPUT_GAMEPAD),
            ]

        return XINPUT_STATE

    def _poll_xinput_dpad(
        self,
        xinput: dict[str, object],
        prev_buttons: int | None,
    ) -> tuple[str | None, int | None]:
        xstate = self._xinput_state_struct()
        state = xstate()
        try:
            rc = int(xinput["get_state"](int(xinput["user_index"]), ctypes.byref(state)))  # type: ignore[index]
        except Exception:
            return None, prev_buttons
        if rc != 0:
            return None, prev_buttons

        buttons = int(state.Gamepad.wButtons)
        if prev_buttons != buttons:
            self.log.info("controller_xinput_buttons buttons=0x%04x", buttons)

        # XINPUT_GAMEPAD_DPAD_* bit masks
        dpad_masks = (
            (0x0001, "up"),
            (0x0008, "right"),
            (0x0002, "down"),
            (0x0004, "left"),
        )

        direction: str | None = None
        if prev_buttons is None:
            prev_buttons = 0
        newly_pressed = buttons & ~prev_buttons
        for mask, name in dpad_masks:
            if newly_pressed & mask:
                direction = name
                break
        return direction, buttons
