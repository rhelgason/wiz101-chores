import pytest

from wiz101_auto.config import load_config


def test_chores_preset(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "mode: reagents\nreagents:\n  hop_zones: 0\npet:\n  auto: false\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.mode == "reagents"
    assert cfg.reagents.hop_zones == 0
    assert cfg.pet.auto is False


def test_unknown_key_and_old_quest_mode(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("mode: quest\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)
    path.write_text("combat:\n  flee_below: 0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)
