from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TargetState:
    value: str | None = None
    fired: bool = False
    error_streak: int = 0
    checked_at: str | None = None


class State:
    def __init__(
        self, targets: dict[str, TargetState] | None = None, meta: dict | None = None
    ) -> None:
        self._targets: dict[str, TargetState] = targets or {}
        self._meta: dict = meta or {}

    @property
    def is_first_run(self) -> bool:
        """True until a run has been recorded — used to send the startup notice once."""
        return not self._meta.get("started_at")

    def mark_started(self, timestamp: str) -> None:
        self._meta.setdefault("started_at", timestamp)

    def mark_notified(self, timestamp: str) -> None:
        self._meta["last_notified_at"] = timestamp

    @property
    def last_notified_at(self) -> str | None:
        return self._meta.get("last_notified_at")

    def _significant(self) -> str:
        """Everything worth a commit — deliberately excludes per-run timestamps.

        Without this the state file changes on every single run, and the
        workflow would push a commit every few minutes forever.
        """
        return json.dumps(
            {
                "meta": {k: v for k, v in sorted(self._meta.items()) if k != "last_run_at"},
                "targets": {
                    name: [ts.value, ts.fired, ts.error_streak]
                    for name, ts in sorted(self._targets.items())
                },
            },
            sort_keys=True,
            ensure_ascii=False,
        )

    def get(self, name: str) -> TargetState:
        return self._targets.get(name, TargetState())

    def set(self, name: str, target_state: TargetState) -> None:
        self._targets[name] = target_state

    def prune(self, known_names: set[str]) -> None:
        """Drop entries for targets that were removed from config."""
        for name in list(self._targets):
            if name not in known_names:
                del self._targets[name]

    def to_dict(self) -> dict:
        return {
            "meta": self._meta,
            "targets": {name: asdict(ts) for name, ts in sorted(self._targets.items())},
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "State":
        targets = {}
        for name, data in (raw.get("targets") or {}).items():
            targets[name] = TargetState(
                value=data.get("value"),
                fired=bool(data.get("fired", False)),
                error_streak=int(data.get("error_streak", 0)),
                checked_at=data.get("checked_at"),
            )
        return cls(targets, dict(raw.get("meta") or {}))

    @classmethod
    def load(cls, path: str | Path) -> "State":
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        return cls.from_dict(raw)

    def save(self, path: str | Path) -> bool:
        """Persist the state. Returns True if the file was actually rewritten.

        A run where nothing meaningful moved leaves the file untouched, so the
        CI job has nothing to commit.
        """
        path = Path(path)
        if path.exists() and State.load(path)._significant() == self._significant():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return True
