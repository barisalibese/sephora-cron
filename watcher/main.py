from __future__ import annotations

import argparse
import logging
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config, Target, load_config
from .errors import ConfigError, WatcherError
from .fetcher import fetch, read_value
from .notifier import (
    EmailConfig,
    Failure,
    Hit,
    Reading,
    build_heartbeat_message,
    build_message,
    build_startup_message,
    build_test_message,
    describe_smtp_error,
    inspect_credentials,
    send,
)
from .rules import evaluate, should_notify, should_report_failure
from .state import State, TargetState

log = logging.getLogger("watcher")

_DRY_RUN_EMAIL = EmailConfig("dry-run", 0, "dry-run", "", "dry-run@local", ["dry-run@local"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def describe(target: Target) -> str:
    if target.condition == "changed":
        return "changed"
    return f"{target.condition} {target.value}"


@dataclass
class CheckResult:
    hit: Hit | None
    failure: Failure | None
    reading: Reading
    new_state: TargetState


def is_heartbeat_due(state: State, days: int, now: str) -> bool:
    """True when nothing has been mailed for `days` — silence must never be ambiguous."""
    if days < 1:
        return False
    last = state.last_notified_at
    if not last:
        return False
    try:
        return datetime.fromisoformat(now) - datetime.fromisoformat(last) >= timedelta(days=days)
    except ValueError:
        return False


def check_target(
    target: Target, state: State, config: Config, fetcher=fetch, now: str | None = None
) -> CheckResult:
    previous = state.get(target.name)
    stamp = now or _now()
    rule = describe(target)

    try:
        payload = fetcher(target, timeout=config.settings.timeout)
        current = read_value(payload, target)
    except WatcherError as exc:
        streak = previous.error_streak + 1
        log.warning("%s: %s (streak %d)", target.name, exc, streak)
        return CheckResult(
            hit=None,
            failure=(
                Failure(target.name, target.url, str(exc), streak)
                if should_report_failure(streak, config.settings.notify_on_error_after)
                else None
            ),
            reading=Reading(target.name, target.url, rule, None, str(exc)),
            new_state=TargetState(
                value=previous.value,
                fired=previous.fired,
                error_streak=streak,
                checked_at=stamp,
            ),
        )

    satisfied = evaluate(target, current, previous.value)
    notify = should_notify(target, satisfied, previous.fired)
    log.info("%s: value=%r satisfied=%s notify=%s", target.name, current, satisfied, notify)

    return CheckResult(
        hit=Hit(target.name, target.url, rule, current, previous.value) if notify else None,
        failure=None,
        reading=Reading(target.name, target.url, rule, current, None, triggered=satisfied),
        new_state=TargetState(value=current, fired=satisfied, error_streak=0, checked_at=stamp),
    )


def _emit(message, dry_run: bool, email_config: EmailConfig | None) -> None:
    if dry_run:
        print(message["Subject"])
        print()
        print(message.get_body(("plain",)).get_content())
        return
    send(email_config, message)


def run(
    config_path: str | Path,
    state_path: str | Path,
    dry_run: bool = False,
    fetcher=fetch,
    now=_now,
) -> int:
    config = load_config(config_path)
    state = State.load(state_path)
    first_run = state.is_first_run
    state.prune({t.name for t in config.targets})
    timestamp = now()

    hits: list[Hit] = []
    failures: list[Failure] = []
    readings: list[Reading] = []

    for target in config.targets:
        result = check_target(target, state, config, fetcher=fetcher, now=timestamp)
        if result.hit:
            hits.append(result.hit)
        if result.failure:
            failures.append(result.failure)
        readings.append(result.reading)
        state.set(target.name, result.new_state)

    state.mark_started(timestamp)

    send_startup = first_run and config.settings.startup_notice
    send_heartbeat = (
        not send_startup
        and not hits
        and not failures
        and is_heartbeat_due(state, config.settings.heartbeat_days, timestamp)
    )

    if not send_startup and not send_heartbeat and not hits and not failures:
        # Nothing to say. The save is still worth attempting: it is a no-op
        # unless a tracked value actually moved.
        if dry_run:
            log.info("nothing to report (dry run, state untouched)")
            return 0
        wrote = state.save(state_path)
        log.info("nothing to report (state %s)", "updated" if wrote else "unchanged")
        return 0

    email_config = _DRY_RUN_EMAIL if dry_run else EmailConfig.from_env()

    if send_startup:
        # First run folds everything into the self-test mail, so a target that is
        # already available on day one still reaches you — in one mail, not two.
        message = build_startup_message(email_config, readings, config.settings.subject_prefix)
        label = f"startup notice for {len(readings)} target(s)"
    elif send_heartbeat:
        message = build_heartbeat_message(
            email_config, readings, config.settings.subject_prefix, config.settings.heartbeat_days
        )
        label = "heartbeat"
    else:
        message = build_message(email_config, hits, failures, config.settings.subject_prefix)
        label = f"{len(hits)} hit(s), {len(failures)} failure(s)"

    # Send before persisting: if SMTP fails, the run is retried whole rather
    # than recording a notification that never arrived.
    _emit(message, dry_run, email_config)
    if dry_run:
        # A dry run is a preview. Persisting here would consume the first-run
        # flag and the deployed watcher would never send its self-test.
        log.info("dry run: would have sent %s (state untouched)", label)
        return 0
    state.mark_notified(timestamp)
    state.save(state_path)
    log.info("sent e-mail: %s", label)
    return 0


def send_test_mail(prefix: str = "[watcher]") -> int:
    """Prove the SMTP credentials work, without waiting for a cron run."""
    try:
        email_config = EmailConfig.from_env()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    log.info(
        "sending as %s via %s:%s (%s), password %d chars",
        email_config.username,
        email_config.host,
        email_config.port,
        "SSL" if email_config.use_ssl else "STARTTLS",
        len(email_config.password),
    )
    for warning in inspect_credentials(email_config):
        log.warning("%s", warning)

    try:
        send(email_config, build_test_message(email_config, prefix))
    except (smtplib.SMTPException, OSError) as exc:
        log.error("test mail failed.\n%s", describe_smtp_error(exc))
        return 3

    log.info("test mail sent to %s — check the inbox", ", ".join(email_config.recipients))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watcher", description="Watch, compare, e-mail.")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--state", default="state/state.json", help="path to the state file")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the e-mail instead of sending it"
    )
    parser.add_argument(
        "--test-mail",
        action="store_true",
        help="send one test e-mail and exit, to verify SMTP credentials",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.test_mail:
        return send_test_mail()

    try:
        return run(args.config, args.state, dry_run=args.dry_run)
    except WatcherError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
