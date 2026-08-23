from __future__ import annotations

import json
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from .config import Target
from .errors import ExtractionError, FetchError

_PATH_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _get(target: Target, timeout: int, session: requests.Session | None):
    client = session or requests
    try:
        response = client.get(target.url, headers=target.request_headers(), timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"{target.name}: request to {target.url} failed — {exc}") from exc
    return response


def fetch_html(target: Target, timeout: int = 20, session: requests.Session | None = None) -> str:
    response = _get(target, timeout, session)
    response.encoding = response.encoding or response.apparent_encoding
    return response.text


def fetch_json(target: Target, timeout: int = 20, session: requests.Session | None = None) -> Any:
    response = _get(target, timeout, session)
    try:
        return response.json()
    except ValueError as exc:
        raise FetchError(f"{target.name}: {target.url} did not return valid JSON — {exc}") from exc


def fetch(target: Target, timeout: int = 20, session: requests.Session | None = None) -> Any:
    """Fetch a target's raw payload: parsed JSON for json targets, HTML text otherwise."""
    if target.source == "json":
        return fetch_json(target, timeout=timeout, session=session)
    return fetch_html(target, timeout=timeout, session=session)


def _path_tokens(path: str) -> list[str | int]:
    return [int(idx) if idx else key for key, idx in _PATH_TOKEN_RE.findall(path)]


def resolve_json_path(data: Any, path: str, target_name: str) -> Any:
    """Walk a dotted path with optional [n] indexes, e.g. `a.b[0].c`.

    Keys containing a literal '.', '[' or ']' cannot be addressed.
    """
    current = data
    walked: list[str] = []
    for token in _path_tokens(path):
        walked.append(str(token))
        here = ".".join(walked)
        if isinstance(token, int):
            if not isinstance(current, list):
                raise ExtractionError(
                    f"{target_name}: json_path '{here}' indexes a {type(current).__name__}, not a list"
                )
            if token >= len(current):
                raise ExtractionError(
                    f"{target_name}: json_path '{here}' is out of range "
                    f"({len(current)} item(s))"
                )
            current = current[token]
            continue
        if not isinstance(current, dict):
            raise ExtractionError(
                f"{target_name}: json_path '{here}' expects an object, "
                f"got {type(current).__name__}"
            )
        if token not in current:
            available = ", ".join(sorted(current)[:8]) or "(empty object)"
            raise ExtractionError(
                f"{target_name}: json_path '{here}' not found. Available keys: {available}"
            )
        current = current[token]
    return current


def _stringify(value: Any, mode: str, target_name: str, path: str) -> str:
    if mode == "count":
        if not isinstance(value, (list, dict, str)):
            raise ExtractionError(
                f"{target_name}: json_mode 'count' needs a list/object/string at '{path}', "
                f"got {type(value).__name__}"
            )
        return str(len(value))
    if mode == "text":
        return "" if value is None else str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def extract_json(data: Any, target: Target) -> str:
    value = resolve_json_path(data, target.json_path, target.name) if target.json_path else data
    text = _stringify(value, target.json_mode, target.name, target.json_path or "$")

    if target.regex_extract:
        text = _apply_regex(text, target.regex_extract, target.name)
    return text


def _apply_regex(value: str, pattern: str, target_name: str) -> str:
    match = re.search(pattern, value, re.DOTALL)
    if not match:
        raise ExtractionError(
            f"{target_name}: regex_extract {pattern!r} matched nothing in {value[:120]!r}"
        )
    return (match.group(1) if match.groups() else match.group(0)).strip()


def extract(html: str, target: Target) -> str:
    soup = BeautifulSoup(html, "html.parser")

    if not target.selector:
        value = soup.get_text(" ", strip=True)
    else:
        nodes = soup.select(target.selector)
        if not nodes:
            raise ExtractionError(
                f"{target.name}: selector {target.selector!r} matched no element on {target.url}"
            )
        if target.index >= len(nodes):
            raise ExtractionError(
                f"{target.name}: selector {target.selector!r} matched {len(nodes)} element(s), "
                f"index {target.index} is out of range"
            )
        node = nodes[target.index]
        if target.attr:
            raw = node.get(target.attr)
            if raw is None:
                raise ExtractionError(
                    f"{target.name}: matched element has no attribute {target.attr!r}"
                )
            value = " ".join(raw) if isinstance(raw, list) else str(raw)
        else:
            value = node.get_text(" ", strip=True)

    value = re.sub(r"\s+", " ", value).strip()

    if target.regex_extract:
        value = _apply_regex(value, target.regex_extract, target.name)

    if not value:
        raise ExtractionError(f"{target.name}: extracted an empty value from {target.url}")
    return value


def read_value(payload: Any, target: Target) -> str:
    """Turn a fetched payload into the single string this target tracks."""
    if target.source == "json":
        return extract_json(payload, target)
    return extract(payload, target)
