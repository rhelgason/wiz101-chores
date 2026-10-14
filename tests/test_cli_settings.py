"""Every setting as a command-line flag (the player, 10-13: no more editing config.yaml)."""

import argparse
from dataclasses import fields

import pytest

from wiz101_auto import cli
from wiz101_auto.config import SECTIONS, Config, load_config, parse_hunt


def parse(*argv):
    parser = argparse.ArgumentParser()
    cli.add_setting_flags(parser)
    return cli.setting_overrides(parser.parse_args(list(argv)))


def test_every_setting_has_a_flag():
    parser = argparse.ArgumentParser()
    cli.add_setting_flags(parser)
    dests = {a.dest for a in parser._actions}
    cfg = Config()
    for section in SECTIONS:
        for f in fields(getattr(cfg, section)):
            key = f"{section}.{f.name}"
            assert key in cli.SKIP_FLAGS or "set:" + key in dests, key


def test_flags_override_and_validate():
    got = parse("--leash", "4000", "--no-sell", "--data-jumps", "--zones", "A", "--gather-zones", "B")
    assert got == {
        "seeds.leash": 4000.0,
        "seeds.sell": False,
        "safety.data_jumps": True,
        "gather.zones": ["A", "B"],
    }
    cfg = load_config(None, got)
    assert cfg.seeds.leash == 4000.0 and not cfg.seeds.sell and cfg.safety.data_jumps
    assert parse() == {}
    with pytest.raises(ValueError):
        load_config(None, {"seeds.leash": 100.0})


def test_hunt_flags():
    got = parse("--hunt", "Charmed Slave:30", "--hunt", "O'Leary Prowler@Marleybone/Zone")
    assert got["seeds.hunts"] == [
        {"creature": "Charmed Slave", "count": 30},
        {"creature": "O'Leary Prowler", "zone": "Marleybone/Zone"},
    ]
    assert parse_hunt("Charmed Slaves") == {"creature": "Charmed Slaves"}
    with pytest.raises(ValueError):
        parse_hunt("Charmed Slave:lots")
    with pytest.raises(ValueError):
        parse_hunt(":30")
