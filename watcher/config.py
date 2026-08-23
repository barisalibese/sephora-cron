from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

VALUE_FREE_CONDITIONS = {"changed"}
NUMERIC_CONDITIONS = {"gt", "gte", "lt", "lte"}
TEXT_CONDITIONS = {"contains", "not_contains", "regex", "equals"}
VALID_CONDITIONS = VALUE_FREE_CONDITIONS | NUMERIC_CONDITIONS | TEXT_CONDITIONS
VALID_SOURCES = {"html", "json"}
VALID_JSON_MODES = {"raw", "count", "text"}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    source: str = "html"
    json_path: str | None = None
    json_mode: str = "raw"
    selector: str | None = None
    attr: str | None = None
    index: int = 0
    regex_extract: str | None = None
    condition: str = "changed"
    value: Any = None
    repeat: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    def request_headers(self) -> dict[str, str]:
        merged = {"User-Agent": DEFAULT_USER_AGENT}
        merged.update(self.headers)
        return merged


@dataclass(frozen=True)
class Settings:
    timeout: int = 20
    notify_on_error_after: int = 3
    subject_prefix: str = "[watcher]"
    startup_notice: bool = True
    heartbeat_days: int = 7


@dataclass(frozen=True)
class Config:
    targets: list[Target]
    settings: Settings = field(default_factory=Settings)


def _require(raw: dict, key: str, index: int) -> Any:
    if key not in raw or raw[key] in (None, ""):
        raise ConfigError(f"targets[{index}]: '{key}' is required")
    return raw[key]


def _parse_target(raw: Any, index: int) -> Target:
    if not isinstance(raw, dict):
        raise ConfigError(f"targets[{index}]: must be a mapping, got {type(raw).__name__}")

    name = str(_require(raw, "name", index))
    url = str(_require(raw, "url", index))

    condition = str(raw.get("condition", "changed"))
    if condition not in VALID_CONDITIONS:
        raise ConfigError(
            f"targets[{index}] ({name}): unknown condition '{condition}'. "
            f"Valid: {', '.join(sorted(VALID_CONDITIONS))}"
        )

    value = raw.get("value")
    if condition not in VALUE_FREE_CONDITIONS and value in (None, ""):
        raise ConfigError(f"targets[{index}] ({name}): condition '{condition}' requires 'value'")
    if condition in NUMERIC_CONDITIONS:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"targets[{index}] ({name}): condition '{condition}' needs a numeric 'value', "
                f"got {value!r}"
            ) from exc

    json_path = raw.get("json_path")
    source = str(raw.get("source") or ("json" if json_path else "html"))
    if source not in VALID_SOURCES:
        raise ConfigError(
            f"targets[{index}] ({name}): unknown source '{source}'. "
            f"Valid: {', '.join(sorted(VALID_SOURCES))}"
        )

    json_mode = str(raw.get("json_mode", "raw"))
    if json_mode not in VALID_JSON_MODES:
        raise ConfigError(
            f"targets[{index}] ({name}): unknown json_mode '{json_mode}'. "
            f"Valid: {', '.join(sorted(VALID_JSON_MODES))}"
        )
    if source == "html" and json_path:
        raise ConfigError(
            f"targets[{index}] ({name}): 'json_path' is only valid when source is 'json'"
        )
    if source == "json" and raw.get("selector"):
        raise ConfigError(
            f"targets[{index}] ({name}): 'selector' is only valid when source is 'html'"
        )

    headers = raw.get("headers") or {}
    if not isinstance(headers, dict):
        raise ConfigError(f"targets[{index}] ({name}): 'headers' must be a mapping")

    return Target(
        name=name,
        url=url,
        source=source,
        json_path=json_path,
        json_mode=json_mode,
        selector=raw.get("selector"),
        attr=raw.get("attr"),
        index=int(raw.get("index", 0)),
        regex_extract=raw.get("regex_extract"),
        condition=condition,
        value=value,
        repeat=bool(raw.get("repeat", False)),
        headers={str(k): str(v) for k, v in headers.items()},
    )


def parse_config(raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    raw_targets = raw.get("targets")
    if not raw_targets:
        raise ConfigError("config must define at least one entry under 'targets'")
    if not isinstance(raw_targets, list):
        raise ConfigError("'targets' must be a list")

    targets = [_parse_target(item, i) for i, item in enumerate(raw_targets)]

    seen: set[str] = set()
    for target in targets:
        if target.name in seen:
            raise ConfigError(f"duplicate target name '{target.name}' — names must be unique")
        seen.add(target.name)

    raw_settings = raw.get("settings") or {}
    if not isinstance(raw_settings, dict):
        raise ConfigError("'settings' must be a mapping")

    settings = Settings(
        timeout=int(raw_settings.get("timeout", 20)),
        notify_on_error_after=int(raw_settings.get("notify_on_error_after", 3)),
        subject_prefix=str(raw_settings.get("subject_prefix", "[watcher]")),
        startup_notice=bool(raw_settings.get("startup_notice", True)),
        heartbeat_days=int(raw_settings.get("heartbeat_days", 7)),
    )
    return Config(targets=targets, settings=settings)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        try:
            raw = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(raw)
