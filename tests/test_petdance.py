import asyncio

from wiz101_auto import petdance


def test_moves_decode_to_wasd():
    assert petdance.decode_moves(b"acbd\0\0\0\0") == "WSDA"
    assert petdance.decode_moves(b"\0" * 8) == ""


def test_energy_numbers():
    assert petdance.first_number("Energy: 12/45") == 12
    assert petdance.first_number("Cost: 5") == 5
    assert petdance.first_number("") is None


def test_post_key_holds_then_releases():
    sent = []

    def post(message, vk, lparam):
        sent.append((message, vk, lparam))

    asyncio.run(petdance.post_key(1, "X", 0.12, post=post))
    assert sent[0] == (petdance.WM_KEYDOWN, ord("X"), 0)
    assert sent[-1][0] == petdance.WM_KEYUP
    assert sent[-1][1] == ord("X")
    assert sum(message == petdance.WM_KEYDOWN for message, _, _ in sent) >= 2
    sent.clear()
    asyncio.run(petdance.post_key(1, 87, 0.06, post=post))
    assert sent[0] == (petdance.WM_KEYDOWN, 87, 0)
    assert sent[-1] == (petdance.WM_KEYUP, 87, 0)


def test_wine_send_key_posts_the_keycode(monkeypatch):
    posted = []

    async def fake(hwnd, key, seconds=0.3, *, post=None):
        posted.append((hwnd, key, seconds))

    monkeypatch.setattr(petdance, "post_key", fake)

    class Client:
        window_handle = 7

    client = Client()
    petdance.install_wine_keys(client)

    class Key:
        value = 87

    asyncio.run(client.send_key(Key(), 0.2))
    assert posted == [(7, 87, 0.2)]
    petdance.install_wine_keys(client)
    asyncio.run(client.send_key(Key(), 0.2))
    assert posted == [(7, 87, 0.2), (7, 87, 0.2)]


def test_request(tmp_path, monkeypatch):
    monkeypatch.setattr(petdance, "PET_REQUEST", tmp_path / "pet.request")
    assert petdance.games_requested() is None
    (tmp_path / "pet.request").write_text("0")
    assert petdance.games_requested() == 0
    (tmp_path / "pet.request").write_text("3")
    assert petdance.games_requested() == 3


def test_a_window_price_is_remembered_as_the_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(petdance, "PET_STATE", tmp_path / "pet.json")
    assert petdance.stage_for_cost(6) == "adult"
    assert petdance.stage_for_cost(1) is None
    petdance.remember_dance(6)
    saved = petdance.load_pet()
    assert saved["dance_cost"] == 6
    assert saved["stage"] == "adult"
    petdance.remember_dance(None)
    assert petdance.load_pet()["stage"] == "adult"


def test_stage_and_goal():
    assert petdance.stage_in(["Your pet is now an Adult!", "Teen"]) == "adult"
    assert petdance.stage_in(["nothing here"]) is None
    assert petdance.kind_in(["Rudy the Bloodbat"], ["bloodbat"]) == "bloodbat"
    goals = {"bloodbat": "adult"}
    assert not petdance.goal_reached("bloodbat", "teen", goals, "mega")
    assert petdance.goal_reached("bloodbat", "adult", goals, "mega")
    assert not petdance.goal_reached("wolf", "adult", goals, "mega")
    assert petdance.goal_reached("wolf", "mega", goals, "mega")
