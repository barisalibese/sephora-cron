from __future__ import annotations

import os
import smtplib
import socket
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape

from .errors import ConfigError


def _clean(value: str) -> tuple[str, bool]:
    """Strip whitespace and one layer of matching quotes.

    A value pasted as `"abcd"` survives `source .env` because the shell eats the
    quotes, then fails in GitHub Actions where nothing does. Same secret, same
    file, different outcome — so normalise it here and say so.
    """
    stripped = value.strip()
    for quote in ('"', "'"):
        if len(stripped) > 1 and stripped[0] == quote and stripped[-1] == quote:
            return stripped[1:-1], True
    return stripped, stripped != value


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: list[str]
    use_ssl: bool = True
    sanitized: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "EmailConfig":
        env = env if env is not None else dict(os.environ)
        missing = [k for k in ("SMTP_USER", "SMTP_PASSWORD", "MAIL_TO") if not env.get(k)]
        if missing:
            raise ConfigError(
                "missing e-mail environment variable(s): " + ", ".join(missing) +
                " — see .env.example"
            )
        cleaned, sanitized = {}, []
        for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO"):
            if key in env and env[key] is not None:
                cleaned[key], changed = _clean(str(env[key]))
                if changed:
                    sanitized.append(key)

        username = cleaned["SMTP_USER"]
        return cls(
            host=cleaned.get("SMTP_HOST") or "smtp.gmail.com",
            port=int(cleaned.get("SMTP_PORT") or 465),
            username=username,
            password=cleaned["SMTP_PASSWORD"],
            sender=cleaned.get("MAIL_FROM") or username,
            recipients=[a.strip() for a in cleaned["MAIL_TO"].split(",") if a.strip()],
            use_ssl=str(env.get("SMTP_SSL", "true")).strip().strip("\"'").lower() != "false",
            sanitized=tuple(sanitized),
        )


@dataclass(frozen=True)
class Hit:
    name: str
    url: str
    condition: str
    current: str
    previous: str | None


@dataclass(frozen=True)
class Reading:
    """What a target evaluated to on this run — used by the startup notice."""

    name: str
    url: str
    rule: str
    value: str | None
    error: str | None
    triggered: bool = False


@dataclass(frozen=True)
class Failure:
    name: str
    url: str
    message: str
    streak: int


def build_subject(hits: list[Hit], failures: list[Failure], prefix: str) -> str:
    if hits and len(hits) == 1:
        return f"{prefix} {hits[0].name}: {hits[0].current}"
    if hits:
        return f"{prefix} {len(hits)} target(s) triggered"
    return f"{prefix} {len(failures)} target(s) failing"


def _plain_body(hits: list[Hit], failures: list[Failure]) -> str:
    lines: list[str] = []
    for hit in hits:
        lines += [
            f"* {hit.name}",
            f"  rule     : {hit.condition}",
            f"  now      : {hit.current}",
            f"  previous : {hit.previous if hit.previous is not None else '(first run)'}",
            f"  url      : {hit.url}",
            "",
        ]
    if failures:
        lines.append("Failing targets:")
        lines += [f"* {f.name} ({f.streak}x): {f.message}" for f in failures]
        lines.append("")
    return "\n".join(lines)


def _html_body(hits: list[Hit], failures: list[Failure]) -> str:
    blocks: list[str] = ["<div style=\"font-family:system-ui,sans-serif;font-size:14px\">"]
    for hit in hits:
        previous = escape(hit.previous) if hit.previous is not None else "<i>(first run)</i>"
        blocks.append(
            "<div style=\"border-left:3px solid #2d7;padding:4px 12px;margin:0 0 16px\">"
            f"<h3 style=\"margin:0 0 6px\">{escape(hit.name)}</h3>"
            f"<div><b>{escape(hit.current)}</b> "
            f"<span style=\"color:#888\">(önceki: {previous})</span></div>"
            f"<div style=\"color:#888\">kural: {escape(hit.condition)}</div>"
            f"<div><a href=\"{escape(hit.url)}\">{escape(hit.url)}</a></div>"
            "</div>"
        )
    if failures:
        blocks.append("<h4 style=\"margin:16px 0 6px\">Failing targets</h4><ul>")
        blocks += [
            f"<li>{escape(f.name)} ({f.streak}x): {escape(f.message)}</li>" for f in failures
        ]
        blocks.append("</ul>")
    blocks.append("</div>")
    return "".join(blocks)


def build_message(
    config: EmailConfig, hits: list[Hit], failures: list[Failure], prefix: str = "[watcher]"
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = build_subject(hits, failures, prefix)
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(_plain_body(hits, failures))
    message.add_alternative(_html_body(hits, failures), subtype="html")
    return message


def build_status_message(
    config: EmailConfig,
    readings: list[Reading],
    prefix: str = "[watcher]",
    headline: str = "Watcher is live",
    intro: str = "",
) -> EmailMessage:
    """The 'here is where every target stands' mail — used for startup and heartbeat.

    Its job is to make silence unambiguous: as long as these arrive, the bot is
    running and every path still resolves.
    """
    armed = sum(1 for r in readings if r.error is None)
    triggered = [r for r in readings if r.triggered]

    message = EmailMessage()
    message["Subject"] = (
        f"{prefix} {headline} — {armed}/{len(readings)} target(s) armed"
        + (" — ALREADY AVAILABLE" if triggered else "")
    )
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)

    lines = [intro, ""] if intro else []
    for reading in readings:
        status = "TRIGGERED NOW" if reading.triggered else ("ERROR" if reading.error else "watching")
        lines += [
            f"* {reading.name} [{status}]",
            f"  rule  : {reading.rule}",
            f"  value : {reading.error or reading.value}",
            f"  url   : {reading.url}",
            "",
        ]
    message.set_content("\n".join(lines))

    blocks = ['<div style="font-family:system-ui,sans-serif;font-size:14px">']
    if intro:
        blocks.append(f"<p>{escape(intro)}</p>")
    for reading in readings:
        colour = "#d33" if reading.error else ("#2d7" if reading.triggered else "#bbb")
        label = "TRIGGERED NOW" if reading.triggered else ("ERROR" if reading.error else "watching")
        blocks.append(
            f'<div style="border-left:3px solid {colour};padding:4px 12px;margin:0 0 14px">'
            f'<h3 style="margin:0 0 4px">{escape(reading.name)} '
            f'<span style="font-weight:400;color:#888">— {label}</span></h3>'
            f"<div>value: <b>{escape(str(reading.error or reading.value))}</b></div>"
            f'<div style="color:#888">rule: {escape(reading.rule)}</div>'
            f'<div><a href="{escape(reading.url)}">{escape(reading.url)}</a></div>'
            "</div>"
        )
    blocks.append("</div>")
    message.add_alternative("".join(blocks), subtype="html")
    return message


def build_startup_message(
    config: EmailConfig, readings: list[Reading], prefix: str = "[watcher]"
) -> EmailMessage:
    return build_status_message(
        config,
        readings,
        prefix,
        headline="Watcher is live",
        intro=(
            "The watcher ran for the first time and this is its self-test. "
            "You will get another mail only when a rule actually fires."
        ),
    )


def build_heartbeat_message(
    config: EmailConfig, readings: list[Reading], prefix: str = "[watcher]", days: int = 7
) -> EmailMessage:
    return build_status_message(
        config,
        readings,
        prefix,
        headline="Still watching",
        intro=(
            f"Nothing has fired in {days} day(s). This is the periodic proof that the "
            "watcher is still running — if these stop arriving, the job itself has died."
        ),
    )


def build_test_message(config: EmailConfig, prefix: str = "[watcher]") -> EmailMessage:
    """A standalone 'does SMTP work at all' probe, independent of any target."""
    message = EmailMessage()
    message["Subject"] = f"{prefix} SMTP test — it works"
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(
        "If you are reading this, the watcher can send mail.\n\n"
        f"host      : {config.host}:{config.port} ({'SSL' if config.use_ssl else 'STARTTLS'})\n"
        f"login     : {config.username}\n"
        f"from      : {config.sender}\n"
        f"to        : {', '.join(config.recipients)}\n"
    )
    return message


def inspect_credentials(config: EmailConfig) -> list[str]:
    """Catch the mistakes that are visible before we even dial the server."""
    warnings: list[str] = []
    gmail = "gmail" in config.host

    if config.sanitized:
        warnings.append(
            "Stripped quotes or whitespace from: " + ", ".join(config.sanitized) + ". "
            "It works here because the shell hides it, but GitHub Actions passes the "
            "value literally — set those secrets without quotes."
        )

    if gmail and " " in config.password:
        warnings.append(
            "SMTP_PASSWORD contains spaces. Google displays App Passwords in "
            "groups of four — paste the 16 characters without the spaces."
        )
    elif gmail and len(config.password.replace(" ", "")) != 16:
        warnings.append(
            f"SMTP_PASSWORD is {len(config.password)} characters. A Gmail App "
            "Password is exactly 16 — this looks like a normal account password, "
            "which Gmail always rejects for SMTP."
        )
    if gmail and config.sender != config.username:
        warnings.append(
            f"MAIL_FROM ({config.sender}) differs from SMTP_USER ({config.username}). "
            "Gmail only sends as the logged-in account or a verified alias."
        )
    if config.port == 587 and config.use_ssl:
        warnings.append("Port 587 needs SMTP_SSL=false (STARTTLS). Port 465 uses SSL.")
    if config.port == 465 and not config.use_ssl:
        warnings.append("Port 465 needs SMTP_SSL=true. Port 587 uses STARTTLS.")
    return warnings


def describe_smtp_error(exc: Exception) -> str:
    """Turn an smtplib failure into the thing you actually need to change."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "Gmail rejected the credentials. In order of likelihood:\n"
            "  1. SMTP_PASSWORD is your normal Google password. It must be a\n"
            "     16-character App Password from myaccount.google.com/apppasswords.\n"
            "  2. 2-Step Verification is off — App Passwords do not exist without it.\n"
            "  3. The App Password was pasted with its spaces or got truncated.\n"
            "  4. SMTP_USER is not the same account the App Password belongs to."
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The server refused every recipient — check MAIL_TO for a typo."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return (
            "The server refused the sender. Gmail only lets you send as the account "
            "you logged in with (or a verified alias) — set MAIL_FROM to SMTP_USER."
        )
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return (
            "The server hung up mid-conversation. Usually a port/encryption mismatch: "
            "use 465 with SMTP_SSL=true, or 587 with SMTP_SSL=false."
        )
    if isinstance(exc, (socket.timeout, TimeoutError, socket.gaierror, ConnectionError)):
        return (
            f"Could not reach the SMTP server ({exc}). Check SMTP_HOST/SMTP_PORT, and "
            "whether outbound port 25/465/587 is blocked on this network."
        )
    return f"Unexpected SMTP failure: {type(exc).__name__}: {exc}"


def send(config: EmailConfig, message: EmailMessage) -> None:
    if config.use_ssl:
        with smtplib.SMTP_SSL(config.host, config.port) as server:
            server.login(config.username, config.password)
            server.send_message(message)
        return
    with smtplib.SMTP(config.host, config.port) as server:
        server.starttls()
        server.login(config.username, config.password)
        server.send_message(message)
