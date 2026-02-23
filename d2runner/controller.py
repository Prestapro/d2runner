from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import sys
import threading
import time


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
            import pygame as _pygame  # type: ignore

            pygame = _pygame
            # Do not call pygame.init() in this background thread on macOS:
            # it initializes SDL video/keyboard and can crash with dispatch
            # assertions inside HIToolbox when not on the main thread.
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
            if joystick.get_numhats() <= self.config.hat_index:
                self.error = (
                    f"controller hat not found at index {self.config.hat_index} "
                    f"(hats={joystick.get_numhats()})"
                )
                self.log.warning("controller_hat_not_found %s", self.error)
                return

            prev_hat_value: tuple[int, int] | None = None

            while not self._stop.is_set():
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

    def _should_throttle(self, direction: str) -> bool:
        now = time.monotonic()
        last = self._last_fired_at.get(direction)
        self._last_fired_at[direction] = now
        if last is None:
            return False
        return (now - last) * 1000 < max(self.config.repeat_guard_ms, 0)
