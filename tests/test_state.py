from watcher.state import State, TargetState


def test_missing_file_yields_empty_state(tmp_path):
    state = State.load(tmp_path / "nope.json")
    assert state.get("anything") == TargetState()


def test_corrupt_file_does_not_crash_the_run(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert State.load(path).get("a").value is None


def test_round_trips_through_disk(tmp_path):
    path = tmp_path / "nested" / "state.json"
    state = State()
    state.set("a", TargetState(value="1.499 TL", fired=True, checked_at="2026-08-23T10:00:00+00:00"))
    state.save(path)

    reloaded = State.load(path)
    assert reloaded.get("a").value == "1.499 TL"
    assert reloaded.get("a").fired is True


def test_prune_drops_targets_removed_from_config():
    state = State()
    state.set("keep", TargetState(value="x"))
    state.set("drop", TargetState(value="y"))
    state.prune({"keep"})
    assert state.to_dict()["targets"] == {
        "keep": {"value": "x", "fired": False, "error_streak": 0, "checked_at": None}
    }


# --- write-only-when-meaningful ------------------------------------------


def test_save_is_a_no_op_when_nothing_meaningful_moved(tmp_path):
    path = tmp_path / "state.json"
    state = State()
    state.set("a", TargetState(value="1", checked_at="2026-08-23T10:00:00+00:00"))
    assert state.save(path) is True

    before = path.read_text(encoding="utf-8")
    later = State.load(path)
    later.set("a", TargetState(value="1", checked_at="2026-08-23T11:00:00+00:00"))

    assert later.save(path) is False, "a new timestamp alone must not rewrite the file"
    assert path.read_text(encoding="utf-8") == before


def test_save_writes_when_a_tracked_value_changes(tmp_path):
    path = tmp_path / "state.json"
    state = State()
    state.set("a", TargetState(value="1"))
    state.save(path)

    state.set("a", TargetState(value="2"))
    assert state.save(path) is True
    assert State.load(path).get("a").value == "2"


def test_save_writes_when_the_error_streak_changes(tmp_path):
    path = tmp_path / "state.json"
    state = State()
    state.set("a", TargetState(value="1", error_streak=0))
    state.save(path)

    state.set("a", TargetState(value="1", error_streak=1))
    assert state.save(path) is True


def test_notification_bookkeeping_round_trips(tmp_path):
    path = tmp_path / "state.json"
    state = State()
    assert state.is_first_run is True
    state.mark_started("2026-08-23T10:00:00+00:00")
    state.mark_notified("2026-08-23T10:00:00+00:00")
    state.save(path)

    reloaded = State.load(path)
    assert reloaded.is_first_run is False
    assert reloaded.last_notified_at == "2026-08-23T10:00:00+00:00"
