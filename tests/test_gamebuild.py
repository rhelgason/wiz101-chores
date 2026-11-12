"""gamebuild: naming a game update when a hook spot is missing."""

from wiz101_auto import gamebuild


def fake_exe(path, stamp, pad=b""):
    head = bytearray(512)
    head[:2] = b"MZ"
    head[60:64] = (128).to_bytes(4, "little")
    head[128:132] = b"PE\0\0"
    head[136:140] = stamp.to_bytes(4, "little")
    path.write_bytes(bytes(head) + pad)
    return path


def test_pe_stamp_and_fingerprint(tmp_path):
    exe = fake_exe(tmp_path / "game.exe", 0x6543_2100)
    assert gamebuild.pe_stamp(exe) == 0x6543_2100
    fp = gamebuild.fingerprint(exe)
    assert fp["stamp"] == 0x6543_2100 and fp["size"] == 512
    assert gamebuild.pe_stamp(tmp_path / "missing.exe") is None
    assert gamebuild.fingerprint(tmp_path / "missing.exe") is None


def test_an_update_is_named_as_one(tmp_path):
    exe = fake_exe(tmp_path / "game.exe", 1)
    record = tmp_path / "game_build.json"
    good = gamebuild.remember_good(exe, record)
    assert gamebuild.last_good(record)["stamp"] == 1
    assert "same game build" in gamebuild.missing_spot_message(gamebuild.fingerprint(exe), good)
    fake_exe(exe, 2, pad=b"x" * 10)  # the update
    text = gamebuild.missing_spot_message(gamebuild.fingerprint(exe), gamebuild.last_good(record))
    assert "was updated" in text and "will not help" in text
    assert "Two causes" in gamebuild.missing_spot_message(gamebuild.fingerprint(exe), None)
