from __future__ import annotations

from pathlib import Path

from eve_client.transaction_state import (
    clear_transaction_state,
    load_transaction_state,
    write_transaction_state,
)


def test_transaction_state_round_trip(tmp_path: Path) -> None:
    payload = {"transaction_id": "abc", "phase": "applying"}
    write_transaction_state(tmp_path, payload)
    loaded = load_transaction_state(tmp_path)
    assert loaded is not None
    assert loaded["transaction_id"] == "abc"
    assert loaded["phase"] == "applying"
    assert isinstance(loaded["pid"], int)
    assert isinstance(loaded["hostname"], str)
    assert isinstance(loaded["started_at"], int)
    assert isinstance(loaded["updated_at"], int)
    clear_transaction_state(tmp_path)
    assert load_transaction_state(tmp_path) is None
