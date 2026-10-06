"""The Savarstaad seed mode: who to fight, when to rest, and what may be sold."""

from wiz101_auto.seeds import (
    BagItem,
    bag_is_full,
    click_is_one_item,
    is_sell_button,
    mana_is_low,
    mana_is_ready,
    names_to_sell,
    sale_kept_the_rest,
)


def test_mana_sends_the_wizard_to_northguard_and_an_unread_bar_does_not():
    assert mana_is_low(0.2)
    assert not mana_is_low(0.4)
    assert not mana_is_low(None)
    assert mana_is_ready(0.6)
    assert mana_is_ready(None)
    assert not mana_is_ready(0.4)


def test_the_bag_sells_when_two_slots_remain():
    assert bag_is_full(98, 100)
    assert not bag_is_full(90, 100)
    assert not bag_is_full(None, 100)
    assert not bag_is_full(10, 0)


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


def test_a_category_button_is_not_an_item_and_the_sell_tab_is_not_the_sale():
    allowed = {"boar hide cap"}
    assert click_is_one_item("Boar Hide Cap", allowed)
    assert not click_is_one_item("All Gear", allowed)
    assert not click_is_one_item("Hats", allowed)
    assert not click_is_one_item("Couch Potato Seed", allowed)
    assert is_sell_button("sellButton", "Sell")
    assert is_sell_button("confirm", "Sell Selected")
    assert not is_sell_button("SellTab", "Sell")
    assert not is_sell_button("sell", "Sell")


def test_a_sale_that_drops_an_old_item_or_a_seed_or_gear_is_rejected():
    assert sale_kept_the_rest({1, 2}, {7}, 3, {1, 2, 9}, {7}, 4)
    assert not sale_kept_the_rest({1, 2}, {7}, 3, {1, 9}, {7}, 3)
    assert not sale_kept_the_rest({1}, {7}, 3, {1}, {7}, 2)
    assert not sale_kept_the_rest({1}, {7}, 3, {1}, {8}, 3)
