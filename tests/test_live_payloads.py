"""Pin raw payload shapes observed on live hardware (2026-09-26).

Fixtures in tests/fixtures/live/ come from a SmartControl account with
boost-disabled `htr` units (no `factory_options.boost_config`). See
api-notes.md, section "Observed behaviour".
"""

import json
from pathlib import Path

from smartbox.models import NodeProg, NodeSetup, NodeStatus

FIXTURES = Path(__file__).parent / "fixtures" / "live"


def _load(name: str) -> dict:
    """Load a JSON fixture from tests/fixtures/live."""
    return json.loads((FIXTURES / name).read_text())


def test_live_status_payload_shape() -> None:
    """Document the boost-disabled htr status family."""
    status = _load("htr_status_boost_off.json")
    assert isinstance(status["error_code"], int)
    for key in ("easy", "runback", "version"):
        assert key in status
    assert "act_duty" not in status
    assert "power_pcb_temp" not in status
    assert status["boost"] is False
    assert status["boost_end_min"] == 0
    assert status["boost_end_day"] == 0


def test_node_status_validation_accepts_live_family() -> None:
    """Regression pin: the live family must validate since the 2.5.3 widening.

    Widened fields (error_code str|int; act_duty/power_pcb_temp optional)
    let raw_response=False consumers use this hardware family. Both status
    families (older fixtures under tests/fixtures/devs + these live ones)
    must validate.
    """
    status = _load("htr_status_boost_off.json")
    model = NodeStatus.model_validate(status)
    assert model.error_code == 0
    assert model.mode == "off"
    assert model.act_duty is None
    assert model.power_pcb_temp is None

    # The older fixture family (str error_code, int act_duty) still validates.
    old = json.loads(
        (
            Path(__file__).parent / "fixtures/devs/device1/htr/0/status.json"
        ).read_text()
    )
    old_model = NodeStatus.model_validate(old)
    assert old_model.error_code == "none"
    assert old_model.act_duty == 45


def test_live_setup_payload_has_no_boost_options() -> None:
    """Boost-disabled units ship no extra_options / boost_config."""
    setup = _load("htr_setup_no_boost.json")
    assert "extra_options" not in setup
    assert "boost_config" not in setup["factory_options"]


def test_socket_status_update_body_is_full_status_not_delta() -> None:
    """Status update frames carry the complete status object."""
    frame = _load("socket_update_status_frame.json")
    assert frame["path"] == "/htr/2/status"
    assert frame["body"] == _load("htr_status_boost_off.json")


def test_socket_version_update_frame_shape() -> None:
    """Setup writes push a version frame for the affected node."""
    frame = _load("socket_update_version_frame.json")
    assert frame["path"] == "/htr/5/version"
    assert set(frame["body"]) == {"fw_version", "hw_version", "pid", "uid"}


def test_socket_sync_lost_frame_is_minimal() -> None:
    """Transient post-write frames carry only sync_status=lost."""
    frame = _load("socket_update_sync_lost_frame.json")
    assert frame["body"] == {"sync_status": "lost"}


def test_live_prog_payload_shape() -> None:
    """Prog GET: string day keys "0".."6", 24 hourly slots, profile ints."""
    for addr in (2, 3, 4, 5, 6):
        prog = _load(f"htr_prog_addr{addr}.json")
        assert set(prog["prog"]) == {str(day) for day in range(7)}
        for day in prog["prog"].values():
            assert len(day) == 24  # prog_resolution 0 => hourly slots
            assert set(day) <= {0, 1, 2}
        assert prog["sync_status"] == "ok"


def test_node_prog_validation_accepts_live_family() -> None:
    """Regression pin: the live prog payload must validate (D6 closed)."""
    model = NodeProg.model_validate(_load("htr_prog_addr5.json"))
    assert model.prog["0"][6] == 1
    assert model.sync_status == "ok"


def test_node_setup_validation_accepts_both_families() -> None:
    """Regression pin: setup widening for the fw-1.9 htr family.

    The fw-1.9 family omits user_duty_factor/flash_version/extra_options
    and adds max_stemp_limit/priority/revision; its factory_options carry
    operating_mode/power_factor/super_lock_available and no prog_resolution
    (D5: schedule resolution not readable from setup on this family).
    """
    new = _load("htr_setup_addr5.json")
    new_model = NodeSetup.model_validate(new)
    assert new_model.user_duty_factor is None
    assert new_model.extra_options is None
    assert new_model.max_stemp_limit == "0.0"
    assert new_model.priority == "low"
    assert new_model.revision == 0
    assert new_model.factory_options.prog_resolution is None
    assert new_model.factory_options.super_lock_available is False

    # The older fixture family (full field set) still validates.
    old = json.loads(
        (
            Path(__file__).parent / "fixtures/devs/device1/htr/0/setup.json"
        ).read_text()
    )
    old_model = NodeSetup.model_validate(old)
    assert old_model.user_duty_factor == 0
    assert old_model.extra_options is not None
    assert old_model.max_stemp_limit is None
    assert old_model.factory_options.prog_resolution == 0
