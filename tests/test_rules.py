import pytest

from watcher.config import Target
from watcher.rules import evaluate, parse_number, should_notify


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1.234,56 TL", 1234.56),
        ("$1,234.56", 1234.56),
        ("12,5", 12.5),
        ("12.5", 12.5),
        ("1.234", 1234.0),
        ("1,234", 1234.0),
        ("Fiyat: 899 TL", 899.0),
        ("-15 derece", -15.0),
        ("100.", 100.0),
    ],
)
def test_parse_number_handles_both_locales(text, expected):
    assert parse_number(text) == expected


def test_parse_number_rejects_text_without_digits():
    with pytest.raises(ValueError):
        parse_number("stokta yok")


def _target(**kwargs):
    return Target(name="t", url="u", **kwargs)


def test_changed_needs_a_previous_value():
    target = _target()
    assert evaluate(target, "A", None) is False
    assert evaluate(target, "A", "A") is False
    assert evaluate(target, "B", "A") is True


@pytest.mark.parametrize(
    "condition, value, current, expected",
    [
        ("lt", 1000, "899 TL", True),
        ("lt", 1000, "1.200 TL", False),
        ("gte", 100, "100", True),
        ("gt", 100, "100", False),
        ("contains", "stokta", "Ürün Stokta", True),
        ("contains", "stokta", "Tükendi", False),
        ("not_contains", "tükendi", "Stokta var", True),
        ("equals", "Açık", "Açık", True),
        ("equals", "Açık", "Kapalı", False),
        ("regex", r"\d{2}:\d{2}", "Saat 19:45 maçı", True),
        ("regex", r"\d{2}:\d{2}", "tarih yok", False),
    ],
)
def test_conditions(condition, value, current, expected):
    target = _target(condition=condition, value=value)
    assert evaluate(target, current, "önceki") is expected


def test_contains_is_case_insensitive():
    assert evaluate(_target(condition="contains", value="STOKTA"), "stokta var", None) is True


def test_edge_triggered_by_default():
    target = _target(condition="lt", value=1000)
    assert should_notify(target, satisfied=True, previously_fired=False) is True
    assert should_notify(target, satisfied=True, previously_fired=True) is False
    assert should_notify(target, satisfied=False, previously_fired=True) is False


def test_repeat_notifies_every_run_while_true():
    target = _target(condition="lt", value=1000, repeat=True)
    assert should_notify(target, satisfied=True, previously_fired=True) is True


# --- failure backoff ------------------------------------------------------

from watcher.rules import should_report_failure  # noqa: E402


def test_failure_is_silent_below_the_threshold():
    assert [s for s in range(1, 3) if should_report_failure(s, 3)] == []


def test_failure_reports_at_the_threshold_then_backs_off_by_doubling():
    assert [s for s in range(1, 60) if should_report_failure(s, 3)] == [3, 6, 12, 24, 48]


def test_a_permanently_broken_target_does_not_mail_every_run():
    """A 5-minute cron over 24h is 288 runs — this must not be 288 mails."""
    mails = sum(1 for streak in range(1, 289) if should_report_failure(streak, 3))
    assert mails <= 8, f"{mails} mails in one day of downtime is spam"


def test_threshold_of_one_reports_immediately_then_backs_off():
    assert [s for s in range(1, 20) if should_report_failure(s, 1)] == [1, 2, 4, 8, 16]
