from __future__ import annotations

import re

from .config import NUMERIC_CONDITIONS, Target

_NUMBER_RE = re.compile(r"-?\d[\d\s.,]*")


def parse_number(text: str) -> float:
    """Pull the first number out of a string, handling both 1.234,56 and 1,234.56.

    Ambiguous input like "1.234" is read as a thousands separator (-> 1234.0),
    which is the right call for TR price pages; "12.5" stays 12.5.
    """
    match = _NUMBER_RE.search(str(text).replace("\xa0", " "))
    if not match:
        raise ValueError(f"no number found in {text!r}")

    raw = match.group(0).replace(" ", "").strip().rstrip(".,")
    has_dot, has_comma = "." in raw, "," in raw

    if has_dot and has_comma:
        decimal_sep = "." if raw.rfind(".") > raw.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        raw = raw.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma:
        parts = raw.split(",")
        raw = raw.replace(",", ".") if len(parts) == 2 and len(parts[1]) != 3 else raw.replace(",", "")
    elif has_dot:
        parts = raw.split(".")
        if not (len(parts) == 2 and len(parts[1]) != 3):
            raw = raw.replace(".", "")

    return float(raw)


def evaluate(target: Target, current: str, previous: str | None) -> bool:
    """Is the target's condition satisfied right now?"""
    condition = target.condition

    if condition == "changed":
        return previous is not None and previous != current

    if condition in NUMERIC_CONDITIONS:
        number = parse_number(current)
        threshold = float(target.value)
        return {
            "gt": number > threshold,
            "gte": number >= threshold,
            "lt": number < threshold,
            "lte": number <= threshold,
        }[condition]

    expected = str(target.value)
    if condition == "contains":
        return expected.casefold() in current.casefold()
    if condition == "not_contains":
        return expected.casefold() not in current.casefold()
    if condition == "equals":
        return current.strip() == expected.strip()
    if condition == "regex":
        return re.search(expected, current) is not None

    raise ValueError(f"unhandled condition {condition!r}")


def should_notify(target: Target, satisfied: bool, previously_fired: bool) -> bool:
    """Edge-triggered by default: notify when the condition turns true, not every run.

    Set `repeat: true` on the target to be told on every run while it stays true.
    """
    if not satisfied:
        return False
    if target.condition == "changed":
        return True
    return target.repeat or not previously_fired


def should_report_failure(streak: int, threshold: int) -> bool:
    """Report a broken target at the threshold, then back off by doubling.

    With a 5-minute cron and threshold 3 that means 15min, 30min, 1h, 2h, 4h …
    — you learn the target is down without getting mailed every single run for
    as long as it stays down.
    """
    if threshold < 1 or streak < threshold:
        return False
    multiple, remainder = divmod(streak, threshold)
    return remainder == 0 and multiple & (multiple - 1) == 0
