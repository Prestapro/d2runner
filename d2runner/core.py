from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import csv
import time
import uuid


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@dataclass
class RunRecord:
    session_id: str
    run_number: int
    started_at: str
    ended_at: str
    duration_ms: int
    duration_sec: float
    note: str = ""


class CsvRunLogger:
    fieldnames = [
        "session_id",
        "run_number",
        "started_at",
        "ended_at",
        "duration_ms",
        "duration_sec",
        "note",
    ]

    def __init__(self, csv_path: Path) -> None:
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def _ensure_header(self) -> None:
        if self.csv_path.exists() and self.csv_path.stat().st_size > 0:
            return
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            writer.writeheader()

    def append(self, record: RunRecord) -> None:
        with self.csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            writer.writerow(
                {
                    "session_id": record.session_id,
                    "run_number": record.run_number,
                    "started_at": record.started_at,
                    "ended_at": record.ended_at,
                    "duration_ms": record.duration_ms,
                    "duration_sec": f"{record.duration_sec:.3f}",
                    "note": record.note,
                }
            )

    def undo_last_for_session(self, session_id: str) -> bool:
        if not self.csv_path.exists():
            return False

        with self.csv_path.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        for idx in range(len(rows) - 1, -1, -1):
            if rows[idx].get("session_id") == session_id:
                del rows[idx]
                with self.csv_path.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                return True
        return False


class RunTracker:
    def __init__(self, logger: CsvRunLogger) -> None:
        self.logger = logger
        self.session_id = self._new_session_id()
        self.run_number = 1
        self.saved_runs_count = 0
        self._running = False
        self._started_monotonic: float | None = None
        self._started_at_dt: datetime | None = None
        self._elapsed_ms_accumulated = 0
        self.last_record: RunRecord | None = None

    @staticmethod
    def _new_session_id() -> str:
        return uuid.uuid4().hex[:8]

    @property
    def is_running(self) -> bool:
        return self._running

    def current_elapsed_ms(self) -> int:
        ms = self._elapsed_ms_accumulated
        if self._running and self._started_monotonic is not None:
            ms += int((time.monotonic() - self._started_monotonic) * 1000)
        return max(ms, 0)

    def formatted_elapsed(self) -> str:
        ms = self.current_elapsed_ms()
        total_sec, ms_part = divmod(ms, 1000)
        minutes, seconds = divmod(total_sec, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms_part // 10:02d}"
        return f"{minutes:02d}:{seconds:02d}.{ms_part // 10:02d}"

    def start(self) -> None:
        if self._running:
            return
        if self._started_at_dt is None:
            self._started_at_dt = _now_local()
        self._started_monotonic = time.monotonic()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._elapsed_ms_accumulated = self.current_elapsed_ms()
        self._started_monotonic = None
        self._running = False

    def toggle_start_stop(self) -> str:
        if self._running:
            self.stop()
            return "stopped"
        self.start()
        return "started"

    def reset_timer(self) -> None:
        self._running = False
        self._started_monotonic = None
        self._started_at_dt = None
        self._elapsed_ms_accumulated = 0

    def reset_session(self) -> None:
        self.reset_timer()
        self.session_id = self._new_session_id()
        self.run_number = 1
        self.saved_runs_count = 0
        self.last_record = None

    def next_run(self, note: str = "", max_saved_runs: int | None = None) -> tuple[RunRecord | None, str]:
        saved: RunRecord | None = None
        if self._started_at_dt is not None:
            was_running = self._running
            if was_running:
                self.stop()

            ended_at = _now_local()
            duration_ms = self.current_elapsed_ms()
            saved = RunRecord(
                session_id=self.session_id,
                run_number=self.run_number,
                started_at=_iso(self._started_at_dt),
                ended_at=_iso(ended_at),
                duration_ms=duration_ms,
                duration_sec=duration_ms / 1000.0,
                note=note.strip(),
            )
            self.logger.append(saved)
            self.last_record = saved
            self.saved_runs_count += 1
            self.run_number += 1

            self.reset_timer()
            if max_saved_runs is not None and self.saved_runs_count >= max_saved_runs:
                return saved, "saved_limit_reached"
            self.start()
            return saved, "saved_and_started_next"

        self.start()
        return None, "started_first_run"

    def undo_last_run(self) -> tuple[bool, str]:
        # Do not allow undo while a current run exists; otherwise numbering can drift.
        if self._started_at_dt is not None or self.current_elapsed_ms() > 0:
            return False, "active_run_present"

        undone = self.logger.undo_last_for_session(self.session_id)
        if not undone:
            return False, "no_rows"

        if self.run_number > 1:
            self.run_number -= 1
        if self.saved_runs_count > 0:
            self.saved_runs_count -= 1
        return True, "ok"
