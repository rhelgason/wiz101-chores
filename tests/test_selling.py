"""Quick selling only this run's loot from the backpack."""

from collections import Counter

from wiz101_auto.selling import (
    BagItem,
    click_is_one_item,
    confirmation_ok,
    count_text,
    names_to_sell,
    new_gear,
    pick_rows,
    sale_kept_the_rest,
    select_all_is_safe,
)


def test_only_this_runs_new_names_are_sold():
    baseline = {1, 2}
    bag = [
        BagItem(1, "Ragged Boots"),
        BagItem(9, "Ragged Boots"),
        BagItem(10, "Boar Hide Cap"),
        BagItem(11, "Boar Hide Cap"),
        BagItem(12, "Couch Potato Seed"),
        BagItem(0, "Nameless Drop"),
        BagItem(13, "Worn Dagger"),
    ]
    sell = names_to_sell(baseline, bag, {"Worn Dagger"})
    assert sell == {"Boar Hide Cap": 2}


def test_a_category_button_is_not_an_item():
    allowed = {"boar hide cap"}
    assert click_is_one_item("Boar Hide Cap", allowed)
    assert not click_is_one_item("All Gear", allowed)
    assert not click_is_one_item("Hats", allowed)
    assert not click_is_one_item("Couch Potato Seed", allowed)


def test_a_sale_that_drops_an_old_item_or_a_seed_or_gear_is_rejected():
    assert sale_kept_the_rest({1, 2}, {7}, 3, {1, 2, 9}, {7}, 4)
    assert not sale_kept_the_rest({1, 2}, {7}, 3, {1, 9}, {7}, 3)
    assert not sale_kept_the_rest({1}, {7}, 3, {1}, {7}, 2)
    assert not sale_kept_the_rest({1}, {7}, 3, {1}, {8}, 3)


def test_rows_are_ticked_by_name_only_as_often_as_they_are_new():
    remaining = Counter({"boar hide cap": 2, "ragged boots": 1})
    labels = ["Boar Hide Cap", "Ragged Boots", "Old Robe", "Boar Hide Cap"]
    labels += ["Boar Hide Cap", "", "Ragged Boots"]
    assert pick_rows(labels, remaining) == [0, 1, 3]
    assert remaining == Counter({"boar hide cap": 0, "ragged boots": 0})
    assert pick_rows(["Couch Potato Seed"], Counter({"couch potato seed": 1})) == []


def test_select_all_only_when_no_row_can_be_older_gear():
    bag = [
        BagItem(1, "Old Hat", "equip_head"),
        BagItem(5, "New Hat", "equip_head"),
        BagItem(6, "New Boots", "equip_feet"),
        BagItem(7, "Couch Potato Seed", "object"),
        BagItem(8, "Rug", "object"),
        BagItem(0, "Unread", "equip_head"),
    ]
    gear = new_gear({1}, bag)
    assert gear == 2
    assert select_all_is_safe(2, gear)
    assert not select_all_is_safe(3, gear)  # an unlocked older item is in the list
    assert not select_all_is_safe(0, gear)


def test_the_games_summary_must_agree_and_never_hold_a_pet():
    assert count_text("1,204") == 1204
    assert count_text("") is None
    assert confirmation_ok(3, 0, 3, by_name=True)
    assert not confirmation_ok(4, 0, 3, by_name=True)
    assert not confirmation_ok(2, 1, 3, by_name=True)
    assert confirmation_ok(None, None, 3, by_name=True)
    assert not confirmation_ok(None, None, 3, by_name=False)


def test_each_view_shows_its_categories_before_rows_are_read(monkeypatch):
    """10-21 03:16: Quick Sell opened on whatever category was ticked last (one
    treasure card), none of the 80 new items was listed, and nothing sold."""
    import asyncio
    from collections import Counter

    from wiz101_auto import selling, ui

    shown = {"list": ["Haze Over Barkingham Cards"]}
    lists = {
        "ShopCategory_All": ["Hallowed Hood", "Old Robe", "Hallowed Hood"],
        "ShopCategory_Jewel": ["Cracked Damage Ruby +2"],
        "ShopCategory_Seed": ["Couch Potato"],
    }
    clicked, sold = [], []

    async def visible(client, name):
        return name

    async def click(client, target):
        clicked.append(target)
        if target in lists:
            shown["list"] = list(lists[target])
        elif target.startswith("ShopCategory_"):
            shown["list"] = []

    async def window(client):
        return "buyWindow"

    async def rows(win):
        return [(label, label, None) for label in shown["list"]]

    async def tick(client, item, box):
        return True

    async def sell(client, win, ticked, *, by_name):
        sold.append(ticked)
        shown["list"] = [label for label in shown["list"] if label == "Old Robe"]
        return True

    async def nothing(*_a, **_k):
        return False

    monkeypatch.setattr(selling, "_visible", visible)
    monkeypatch.setattr(ui, "click_center", click)
    monkeypatch.setattr(selling, "_sell_window", window)
    monkeypatch.setattr(selling, "_page_rows", rows)
    monkeypatch.setattr(selling, "_tick", tick)
    monkeypatch.setattr(selling, "_sell_selected", sell)
    monkeypatch.setattr(selling, "_next_page", nothing)
    monkeypatch.setattr(selling, "_first_page", nothing)
    monkeypatch.setattr(selling, "_fighting", nothing)
    monkeypatch.setattr(selling.asyncio, "sleep", nothing)
    remaining = Counter({"hallowed hood": 2, "cracked damage ruby +2": 1})

    async def run():
        total = 0
        for view in selling.VIEWS:
            total += await selling._sell_view(None, view, remaining, 0)
        return total

    assert asyncio.run(run()) == 2
    assert sold == [2, 1]
    assert "ShopCategory_Seed" not in clicked
    assert not any(remaining.values())


def test_a_tick_clicks_its_box_once_even_when_the_box_reads_unchecked(monkeypatch):
    """10-23: every box read unchecked (a SpellCheckBox offset), the row was
    clicked too, and 270 rows a pass gave 0 sales."""
    import asyncio

    from wiz101_auto import selling, ui

    clicks = []

    class Box:
        async def maybe_checked(self):
            return False

        async def read_base_address(self):
            raise RuntimeError("no memory here")

    async def click(client, target):
        clicks.append(target)

    async def nothing(*_a):
        return None

    box = Box()
    monkeypatch.setattr(ui, "click_center", click)
    monkeypatch.setattr(selling.asyncio, "sleep", nothing)
    assert asyncio.run(selling._tick(None, "row", box)) is None
    assert clicks == [box]


def test_unread_ticks_need_the_games_count_to_sell(monkeypatch):
    import asyncio

    from wiz101_auto import selling

    calls = []

    async def window(client):
        return "buyWindow"

    async def rows(win):
        return [("Hallowed Hood", "row", "box")]

    async def unread(client, item, box):
        return None

    async def sell(client, win, ticked, *, by_name):
        calls.append((ticked, by_name))
        return False

    async def nothing(*_a, **_k):
        return False

    monkeypatch.setattr(selling, "_sell_window", window)
    monkeypatch.setattr(selling, "_page_rows", rows)
    monkeypatch.setattr(selling, "_tick", unread)
    monkeypatch.setattr(selling, "_sell_selected", sell)
    monkeypatch.setattr(selling, "_first_page", nothing)
    monkeypatch.setattr(selling, "_next_page", nothing)
    monkeypatch.setattr(selling, "_fighting", nothing)
    monkeypatch.setattr(selling.blackbox, "event", lambda *a, **k: None)
    asyncio.run(selling._sell_listed(None, "ShopCategory_All", Counter({"hallowed hood": 1}), 0))
    assert calls == [(1, False)]
    assert not confirmation_ok(None, None, 1, by_name=False)
