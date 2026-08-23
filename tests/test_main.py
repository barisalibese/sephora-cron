import pytest
import yaml

from watcher import main as main_module
from watcher.errors import ConfigError, FetchError
from watcher.main import run
from watcher.state import State

QUIET = {"startup_notice": False}


def write_config(tmp_path, targets, settings=None):
    path = tmp_path / "config.yaml"
    payload = {"targets": targets, "settings": {**QUIET, **(settings or {})}}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def fetcher(pages):
    """Serve canned payloads per call, so successive runs can see different data."""
    calls = iter(pages)

    def _fetch(target, timeout=20):
        page = next(calls)
        if isinstance(page, Exception):
            raise page
        return page

    return _fetch


@pytest.fixture
def sent(monkeypatch):
    outbox = []
    monkeypatch.setattr(main_module, "send", lambda config, message: outbox.append(message))
    monkeypatch.setattr(
        main_module.EmailConfig,
        "from_env",
        classmethod(lambda cls, env=None: cls("h", 465, "u", "p", "from@test", ["to@test"])),
    )
    return outbox


def plain(message):
    return message.get_body(("plain",)).get_content()


# --- rule behaviour -------------------------------------------------------


def test_first_run_records_state_without_notifying(tmp_path, sent):
    config = write_config(tmp_path, [{"name": "p", "url": "u", "selector": ".v"}])
    state_path = tmp_path / "state.json"

    assert run(config, state_path, fetcher=fetcher(["<div class='v'>100</div>"])) == 0

    assert sent == []
    assert State.load(state_path).get("p").value == "100"


def test_second_run_notifies_when_the_value_changed(tmp_path, sent):
    config = write_config(tmp_path, [{"name": "p", "url": "u", "selector": ".v"}])
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher(["<div class='v'>100</div>"]))
    run(config, state_path, fetcher=fetcher(["<div class='v'>250</div>"]))

    assert len(sent) == 1
    assert "250" in plain(sent[0]) and "100" in plain(sent[0])
    assert State.load(state_path).get("p").value == "250"


def test_threshold_fires_once_then_stays_quiet(tmp_path, sent):
    config = write_config(
        tmp_path, [{"name": "p", "url": "u", "selector": ".v", "condition": "lt", "value": 1000}]
    )
    state_path = tmp_path / "state.json"
    cheap = "<div class='v'>899 TL</div>"

    run(config, state_path, fetcher=fetcher(["<div class='v'>1.200 TL</div>"]))
    run(config, state_path, fetcher=fetcher([cheap]))
    run(config, state_path, fetcher=fetcher([cheap]))

    assert len(sent) == 1, "edge-triggered: only the transition should mail"


def test_repeat_true_notifies_on_every_run(tmp_path, sent):
    config = write_config(
        tmp_path,
        [{
            "name": "p", "url": "u", "selector": ".v",
            "condition": "lt", "value": 1000, "repeat": True,
        }],
    )
    state_path = tmp_path / "state.json"
    cheap = "<div class='v'>899 TL</div>"

    run(config, state_path, fetcher=fetcher([cheap]))
    run(config, state_path, fetcher=fetcher([cheap]))

    assert len(sent) == 2


# --- resilience -----------------------------------------------------------


def test_fetch_errors_only_mail_after_the_configured_streak(tmp_path, sent):
    config = write_config(
        tmp_path,
        [{"name": "p", "url": "u", "selector": ".v"}],
        settings={"notify_on_error_after": 2},
    )
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([FetchError("p: 503")]))
    assert sent == []

    run(config, state_path, fetcher=fetcher([FetchError("p: 503")]))
    assert len(sent) == 1
    assert "503" in plain(sent[0])


def test_a_failing_target_does_not_block_a_healthy_one(tmp_path, sent):
    config = write_config(
        tmp_path,
        [
            {"name": "broken", "url": "u1", "selector": ".v"},
            {"name": "fine", "url": "u2", "selector": ".v"},
        ],
    )
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([FetchError("boom"), "<div class='v'>100</div>"]))
    run(config, state_path, fetcher=fetcher([FetchError("boom"), "<div class='v'>200</div>"]))

    assert len(sent) == 1
    assert "200" in plain(sent[0])
    assert State.load(state_path).get("broken").error_streak == 2


def test_dry_run_prints_instead_of_sending(tmp_path, sent, capsys):
    config = write_config(tmp_path, [{"name": "p", "url": "u", "selector": ".v"}])
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher(["<div class='v'>100</div>"]))
    run(config, state_path, dry_run=True, fetcher=fetcher(["<div class='v'>250</div>"]))

    assert sent == []
    assert "250" in capsys.readouterr().out


# --- json source ----------------------------------------------------------

PAYLOAD_SOLD_OUT = {"first_step": {"data": {"ticket-market-rates": {"rates": [], "errors": ["nope"]}}}}
PAYLOAD_AVAILABLE = {
    "first_step": {"data": {"ticket-market-rates": {"rates": [{"id": 1}, {"id": 2}], "errors": []}}}
}


def _rates_target():
    return {
        "name": "Tickets",
        "url": "https://api.test/widget",
        "json_path": "first_step.data.ticket-market-rates.rates",
        "json_mode": "count",
        "condition": "gt",
        "value": 0,
    }


def test_json_target_stays_quiet_while_sold_out(tmp_path, sent):
    config = write_config(tmp_path, [_rates_target()])
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))

    assert sent == []
    assert State.load(state_path).get("Tickets").value == "0"


def test_json_target_mails_when_rates_appear(tmp_path, sent):
    config = write_config(tmp_path, [_rates_target()])
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    run(config, state_path, fetcher=fetcher([PAYLOAD_AVAILABLE]))
    run(config, state_path, fetcher=fetcher([PAYLOAD_AVAILABLE]))

    assert len(sent) == 1, "one mail on the transition, not one per run"
    assert "Tickets: 2" in sent[0]["Subject"]


# --- startup notice -------------------------------------------------------


def test_startup_notice_is_sent_once_on_the_very_first_run(tmp_path, sent):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"targets": [_rates_target()]}), encoding="utf-8")
    state_path = tmp_path / "state.json"

    run(path, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    assert len(sent) == 1
    assert "Watcher is live" in sent[0]["Subject"]
    assert "1/1 target(s) armed" in sent[0]["Subject"]
    assert "watching" in plain(sent[0])

    run(path, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    assert len(sent) == 1, "the self-test must not repeat on later runs"


def test_startup_notice_flags_a_target_that_is_already_available(tmp_path, sent):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"targets": [_rates_target()]}), encoding="utf-8")

    run(path, tmp_path / "state.json", fetcher=fetcher([PAYLOAD_AVAILABLE]))

    assert len(sent) == 1, "day-one availability must not cost a second mail"
    assert "ALREADY AVAILABLE" in sent[0]["Subject"]
    assert "TRIGGERED NOW" in plain(sent[0])


def test_startup_notice_reports_a_broken_target_instead_of_hiding_it(tmp_path, sent):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"targets": [_rates_target()]}), encoding="utf-8")

    run(path, tmp_path / "state.json", fetcher=fetcher([FetchError("dns exploded")]))

    assert "0/1 target(s) armed" in sent[0]["Subject"]
    assert "dns exploded" in plain(sent[0])


def test_startup_notice_does_not_suppress_the_first_real_hit(tmp_path, sent):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"targets": [_rates_target()]}), encoding="utf-8")
    state_path = tmp_path / "state.json"

    run(path, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    run(path, state_path, fetcher=fetcher([PAYLOAD_AVAILABLE]))

    assert len(sent) == 2
    assert "Watcher is live" in sent[0]["Subject"]
    assert "Tickets: 2" in sent[1]["Subject"]


# --- heartbeat ------------------------------------------------------------


def _heartbeat_config(tmp_path, days=7):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"targets": [_rates_target()], "settings": {"heartbeat_days": days}}),
        encoding="utf-8",
    )
    return path


def test_heartbeat_proves_the_bot_is_alive_after_a_quiet_stretch(tmp_path, sent):
    config = _heartbeat_config(tmp_path)
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2026-08-23T10:00:00+00:00")
    assert len(sent) == 1  # startup

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2026-08-26T10:00:00+00:00")
    assert len(sent) == 1, "3 days in, still quiet"

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2026-08-30T10:00:00+00:00")
    assert len(sent) == 2, "7 days in, the bot must prove it is alive"
    assert "Still watching" in sent[1]["Subject"]
    assert "the job itself has died" in plain(sent[1])


def test_heartbeat_clock_restarts_after_any_mail(tmp_path, sent):
    config = _heartbeat_config(tmp_path)
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2026-08-23T10:00:00+00:00")
    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2026-08-30T10:00:00+00:00")
    assert len(sent) == 2

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2026-09-02T10:00:00+00:00")
    assert len(sent) == 2, "only 3 days since the heartbeat"


def test_heartbeat_can_be_switched_off(tmp_path, sent):
    config = _heartbeat_config(tmp_path, days=0)
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2026-08-23T10:00:00+00:00")
    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2027-08-23T10:00:00+00:00")
    assert len(sent) == 1


def test_a_real_hit_wins_over_a_due_heartbeat(tmp_path, sent):
    config = _heartbeat_config(tmp_path)
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]), now=lambda: "2026-08-23T10:00:00+00:00")
    run(config, state_path, fetcher=fetcher([PAYLOAD_AVAILABLE]), now=lambda: "2026-09-23T10:00:00+00:00")

    assert "Still watching" not in sent[1]["Subject"], "availability must not become a heartbeat"
    assert "Tickets: 2" in sent[1]["Subject"]


# --- quiet runs leave the repo alone --------------------------------------


def test_a_quiet_run_does_not_rewrite_the_state_file(tmp_path, sent):
    config = write_config(tmp_path, [_rates_target()])
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    before = state_path.read_text(encoding="utf-8")

    run(config, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    assert state_path.read_text(encoding="utf-8") == before, "quiet run must produce no commit"


def test_a_broken_target_still_records_its_streak(tmp_path, sent):
    config = write_config(tmp_path, [_rates_target()], settings={"notify_on_error_after": 99})
    state_path = tmp_path / "state.json"

    run(config, state_path, fetcher=fetcher([FetchError("down")]))
    run(config, state_path, fetcher=fetcher([FetchError("down")]))

    assert State.load(state_path).get("Tickets").error_streak == 2


def test_dry_run_never_touches_the_state_file(tmp_path, sent, capsys):
    """A preview must not consume the first-run flag the deployment relies on."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"targets": [_rates_target()]}), encoding="utf-8")
    state_path = tmp_path / "state.json"

    run(path, state_path, dry_run=True, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    assert not state_path.exists(), "dry run must leave no state behind"
    assert "Watcher is live" in capsys.readouterr().out

    run(path, state_path, fetcher=fetcher([PAYLOAD_SOLD_OUT]))
    assert len(sent) == 1, "the real first run must still send the self-test"
    assert "Watcher is live" in sent[0]["Subject"]


# --- --test-mail ----------------------------------------------------------


def test_test_mail_sends_one_message_and_reports_success(monkeypatch, sent):
    monkeypatch.setattr(main_module, "send", lambda cfg, msg: sent.append(msg))
    assert main_module.send_test_mail() == 0
    assert len(sent) == 1
    assert "SMTP test" in sent[0]["Subject"]


def test_test_mail_reports_missing_env_without_touching_the_network(monkeypatch):
    monkeypatch.setattr(
        main_module.EmailConfig,
        "from_env",
        classmethod(lambda cls, env=None: (_ for _ in ()).throw(ConfigError("missing SMTP_USER"))),
    )
    called = []
    monkeypatch.setattr(main_module, "send", lambda *a: called.append(1))
    assert main_module.send_test_mail() == 2
    assert called == []


def test_test_mail_surfaces_an_auth_failure_as_a_distinct_exit_code(monkeypatch, sent, caplog):
    import smtplib

    def boom(cfg, msg):
        raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    monkeypatch.setattr(main_module, "send", boom)
    assert main_module.send_test_mail() == 3
    assert "App Password" in caplog.text
