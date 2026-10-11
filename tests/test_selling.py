"""Selling only this run's loot at the Bazaar."""

from wiz101_auto.selling import (
    BagItem,
    click_is_one_item,
    is_sell_button,
    names_to_sell,
    sale_kept_the_rest,
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
