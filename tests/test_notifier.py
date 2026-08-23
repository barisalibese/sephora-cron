import pytest

from watcher.errors import ConfigError
from watcher.notifier import EmailConfig, Failure, Hit, build_message, build_subject

CONFIG = EmailConfig(
    host="smtp.test", port=465, username="u", password="p",
    sender="from@test", recipients=["to@test"],
)


def test_from_env_defaults_to_gmail_ssl():
    config = EmailConfig.from_env(
        {"SMTP_USER": "u@gmail.com", "SMTP_PASSWORD": "pw", "MAIL_TO": "a@x.com, b@x.com"}
    )
    assert (config.host, config.port, config.use_ssl) == ("smtp.gmail.com", 465, True)
    assert config.sender == "u@gmail.com"
    assert config.recipients == ["a@x.com", "b@x.com"]


def test_from_env_reports_every_missing_variable():
    with pytest.raises(ConfigError) as exc:
        EmailConfig.from_env({"SMTP_USER": "u"})
    assert "SMTP_PASSWORD" in str(exc.value) and "MAIL_TO" in str(exc.value)


def test_single_hit_subject_carries_the_value():
    hit = Hit("Fiyat", "https://x.test", "lt 1000.0", "899 TL", "1.200 TL")
    assert build_subject([hit], [], "[watcher]") == "[watcher] Fiyat: 899 TL"


def test_multi_hit_and_failure_only_subjects():
    hit = Hit("A", "u", "changed", "1", None)
    assert "2 target(s) triggered" in build_subject([hit, hit], [], "[w]")
    assert "1 target(s) failing" in build_subject([], [Failure("A", "u", "boom", 3)], "[w]")


def test_message_has_both_plain_and_html_parts():
    hit = Hit("Fiyat", "https://x.test", "lt 1000.0", "899 TL", None)
    message = build_message(CONFIG, [hit], [Failure("B", "u2", "timeout", 3)])

    plain = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()

    assert "899 TL" in plain and "(first run)" in plain
    assert "timeout" in plain and "B" in plain
    assert "https://x.test" in html
    assert message["To"] == "to@test"


# --- SMTP diagnostics -----------------------------------------------------

import smtplib  # noqa: E402
import socket  # noqa: E402

from watcher.notifier import build_test_message, describe_smtp_error  # noqa: E402


def test_test_message_states_the_connection_it_used():
    body = build_test_message(CONFIG).get_body(("plain",)).get_content()
    assert "smtp.test:465 (SSL)" in body
    assert "to@test" in body


def test_test_message_never_contains_the_password():
    config = EmailConfig(
        host="smtp.test", port=465, username="u@test", password="abcdefghijklmnop",
        sender="from@test", recipients=["to@test"],
    )
    assert config.password not in str(build_test_message(config))


def test_auth_failure_points_at_the_app_password():
    hint = describe_smtp_error(smtplib.SMTPAuthenticationError(535, b"denied"))
    assert "App Password" in hint
    assert "2-Step Verification" in hint


def test_disconnect_points_at_the_port_encryption_pairing():
    hint = describe_smtp_error(smtplib.SMTPServerDisconnected("bye"))
    assert "465" in hint and "587" in hint


def test_sender_refused_points_at_mail_from():
    assert "MAIL_FROM" in describe_smtp_error(smtplib.SMTPSenderRefused(553, b"no", "a@b"))


def test_network_failures_point_at_host_and_port():
    assert "SMTP_HOST" in describe_smtp_error(socket.gaierror("no such host"))
    assert "SMTP_HOST" in describe_smtp_error(TimeoutError("timed out"))


def test_unknown_errors_are_reported_verbatim_rather_than_guessed_at():
    assert "ValueError" in describe_smtp_error(ValueError("something odd"))


# --- pre-flight credential checks -----------------------------------------

from watcher.notifier import inspect_credentials  # noqa: E402


def _gmail(**kwargs):
    base = dict(
        host="smtp.gmail.com", port=465, username="me@gmail.com",
        password="abcdefghijklmnop", sender="me@gmail.com", recipients=["me@gmail.com"],
    )
    return EmailConfig(**{**base, **kwargs})


def test_a_correct_gmail_config_produces_no_warnings():
    assert inspect_credentials(_gmail()) == []


def test_a_spaced_app_password_is_flagged():
    assert "without the spaces" in " ".join(inspect_credentials(_gmail(password="abcd efgh ijkl mnop")))


def test_a_normal_password_length_is_flagged():
    assert "exactly 16" in " ".join(inspect_credentials(_gmail(password="MySecret123")))


def test_a_mismatched_sender_is_flagged():
    assert "MAIL_FROM" in " ".join(inspect_credentials(_gmail(sender="other@gmail.com")))


@pytest.mark.parametrize(
    "port, ssl, fragment",
    [(587, True, "587 needs SMTP_SSL=false"), (465, False, "465 needs SMTP_SSL=true")],
)
def test_port_and_encryption_mismatches_are_flagged(port, ssl, fragment):
    assert fragment in " ".join(inspect_credentials(_gmail(port=port, use_ssl=ssl)))


def test_non_gmail_hosts_are_not_lectured_about_app_passwords():
    other = EmailConfig("smtp.office365.com", 587, "u", "short", "u", ["t"], use_ssl=False)
    assert inspect_credentials(other) == []


# --- quoted / padded env values -------------------------------------------


def test_quoted_values_are_unwrapped():
    config = EmailConfig.from_env(
        {"SMTP_USER": '"me@gmail.com"', "SMTP_PASSWORD": '"abcdefghijklmnop"',
         "MAIL_TO": "me@gmail.com"}
    )
    assert config.password == "abcdefghijklmnop"
    assert config.username == "me@gmail.com"


def test_unwrapping_is_reported_because_actions_will_not_do_it():
    config = EmailConfig.from_env(
        {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": '"abcdefghijklmnop"',
         "MAIL_TO": "me@gmail.com"}
    )
    assert config.sanitized == ("SMTP_PASSWORD",)
    assert "without quotes" in " ".join(inspect_credentials(config))


def test_trailing_whitespace_is_stripped_and_reported():
    config = EmailConfig.from_env(
        {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "abcdefghijklmnop\n",
         "MAIL_TO": "me@gmail.com"}
    )
    assert config.password == "abcdefghijklmnop"
    assert config.sanitized == ("SMTP_PASSWORD",)


def test_a_clean_config_reports_nothing_sanitized():
    config = EmailConfig.from_env(
        {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "abcdefghijklmnop",
         "MAIL_TO": "me@gmail.com"}
    )
    assert config.sanitized == ()
    assert inspect_credentials(config) == []


def test_a_single_quote_character_is_not_treated_as_a_wrapper():
    config = EmailConfig.from_env(
        {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": '"', "MAIL_TO": "me@gmail.com"}
    )
    assert config.password == '"'
