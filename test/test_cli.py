from vibestorm.app.main import get_status


def test_status_phase() -> None:
    assert get_status().phase == "phase-1-scaffold"
