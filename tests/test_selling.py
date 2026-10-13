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
