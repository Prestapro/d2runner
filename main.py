from __future__ import annotations

from pathlib import Path
import argparse
import logging

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="D2R run timer + counter + CSV logger")
    parser.add_argument(
        "--csv",
        default="runs.csv",
        help="Path to CSV log file (default: runs.csv in current directory)",
    )
    parser.add_argument(
        "--log",
        default="d2runner.log",
        help="Path to app log file (default: d2runner.log in current directory)",
    )
    parser.add_argument(
        "--controller-config",
        default="controller_mapping.json",
        help="Path to controller mapping JSON (default: controller_mapping.json)",
    )
    parser.add_argument(
        "--ui",
        default="auto",
        choices=["auto", "qt", "tk"],
        help="UI backend: auto (prefer Qt), qt, or tk",
    )
    parser.add_argument(
        "--overlay",
        default="off",
        choices=["off", "compact", "mini"],
        help="Overlay mode (Qt UI): off, compact, or mini (run+timer only)",
    )
    return parser.parse_args()


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)


def main() -> None:
    args = parse_args()
    setup_logging(Path(args.log))
    logging.getLogger("d2runner").info(
        "app_start csv=%s log=%s controller_config=%s ui=%s overlay=%s",
        args.csv,
        args.log,
        args.controller_config,
        args.ui,
        args.overlay,
    )
    app = _build_ui_app(args.ui, Path(args.csv), Path(args.controller_config), overlay_mode=args.overlay)
    app.run()


def _build_ui_app(ui_mode: str, csv_path: Path, controller_config_path: Path, overlay_mode: str = "off"):
    log = logging.getLogger("d2runner")
    if ui_mode in {"auto", "qt"}:
        try:
            from d2runner.ui_qt import D2RunnerQtApp

            return D2RunnerQtApp(csv_path, controller_config_path, overlay_mode=overlay_mode)
        except Exception as exc:
            if ui_mode == "qt":
                raise
            log.warning("qt_ui_unavailable fallback=tk error=%s", exc)
    from d2runner.ui import D2RunnerApp

    if overlay_mode != "off":
        log.warning("overlay_mode_not_supported_in_tk overlay=%s", overlay_mode)
    return D2RunnerApp(csv_path, controller_config_path)


if __name__ == "__main__":
    main()
