# D2 Runner MVP (macOS-first, portable to Windows)

External run counter/timer for Diablo II: Resurrected (or any repeated runs), with:

- run number
- timer
- CSV logging
- hotkeys (global via `pynput`, fallback to window hotkeys)
- optional Xbox controller D-pad support via `pygame`
- app logging (`d2runner.log`) for key/action visibility
- per-session CSV files (`runs_YYYY-MM-DD_HH-MM-SS.csv`) on `New Session`
- hard session guard: max `500` saved runs per session (then create a new session)
- Qt UI (PySide6) available for a more native macOS/Windows look; tkinter fallback remains
- packaging scripts + CI workflow for Windows `.exe` and macOS binary
- compact Qt overlay mode to reduce screen obstruction

## Features (MVP)

- macOS hotkeys: `cmd+alt+1..5` (Windows later: `Ctrl+Alt+1..5`)
- `cmd+alt+1` Start / Stop current run timer
- `cmd+alt+2` Next Run:
  - if no run is active: starts run #1
  - if a run is active: saves current run to CSV and immediately starts next run
- `cmd+alt+3` Reset current timer (keeps run number)
- `cmd+alt+4` New session (new `session_id`, reset counter to run #1)
- `cmd+alt+5` Undo last saved row for current session
- Xbox controller (optional): D-pad actions from `controller_mapping.json` (auto-created on first run)
- `Settings` button in UI to remap all actions (keyboard + D-pad) and reload without restart

## CSV schema

- `session_id`
- `run_number`
- `started_at`
- `ended_at`
- `duration_ms`
- `duration_sec`
- `note`

## Session CSV behavior

- The app starts each session with a timestamped CSV file, e.g. `runs_2026-02-24_00-30-12.csv`
- Pressing `New Session` creates a **new CSV file** and resets `session_id` / `run_number`
- A session can contain any number of runs (e.g. `500+`, `1000+`) until you manually start a new session
- Current build has a hard guard at `500` saved runs:
  - the 500th run is saved
  - further run actions are blocked
  - UI asks you to create a new session

## Run (macOS)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --csv runs.csv --log d2runner.log --controller-config controller_mapping.json --ui auto
```

UI backend options:

- `--ui auto` (default): prefer PySide6 Qt UI, fallback to tkinter
- `--ui qt`: force Qt UI (fails if PySide6 missing)
- `--ui tk`: force tkinter UI

Overlay options (Qt UI):

- `--overlay off` (default)
- `--overlay compact` (small top-right overlay, reduced controls so it blocks less of the game)

Example:

```bash
python main.py --ui qt --overlay compact --csv runs.csv --log d2runner.log
```

## Build binaries / exe

Important:

- Windows `.exe` must be built on Windows (PyInstaller does not reliably cross-compile Windows binaries from macOS)
- macOS binary should be built on macOS

Local build scripts:

- Windows: `scripts/build_windows.bat`
- macOS: `scripts/build_macos.sh`

CI workflow (GitHub Actions):

- `.github/workflows/build-binaries.yml`
- builds artifacts for `windows-latest` and `macos-latest`

## macOS permissions for global hotkeys

If global hotkeys do not work, grant permissions to Terminal / iTerm / Python:

- `System Settings -> Privacy & Security -> Accessibility`
- sometimes also `Input Monitoring`

The app still supports hotkeys while its window is focused (same combos).

## App logs

The app writes logs to `d2runner.log` (default), including hotkey/button actions:

- `action_received ...`
- `action_applied ...`
- controller diagnostics:
  - `controller_devices_detected ...`
  - `controller_hat_motion ...`
  - `controller_action ...`

## Input bindings (keyboard + D-pad)

On first run, the app creates `controller_mapping.json` if it does not exist.
You can edit it manually or use the in-app `Settings` button.

Example:

```json
{
  "enabled": true,
  "device_index": 0,
  "hat_index": 0,
  "repeat_guard_ms": 150,
  "keyboard_map": {
    "toggle_start_stop": "cmd+alt+1",
    "next_run": "cmd+alt+2",
    "reset_timer": "cmd+alt+3",
    "reset_session": "cmd+alt+4",
    "undo_last": "cmd+alt+5"
  },
  "dpad_map": {
    "up": "toggle_start_stop",
    "right": "next_run",
    "down": "reset_timer",
    "left": "reset_session"
  }
}
```

Allowed actions in `dpad_map` / `keyboard_map`:

- `toggle_start_stop`
- `next_run`
- `reset_timer`
- `reset_session`
- `undo_last`
- `none`

## Windows port (later)

Core timer/CSV logic is in `d2runner/core.py`. Porting typically only changes:

- hotkey backend (`d2runner/hotkeys.py`) if needed
- packaging (e.g. `pyinstaller`)
