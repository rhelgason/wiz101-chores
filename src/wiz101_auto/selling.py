"""Sell this run's loot at the Wizard City Bazaar, then go back to the farm.

A seeds run reads the backpack when it starts (LootBook). When the bag is
two slots from full, `sell_trip` marks the farm spot, walks to the Bazaar
(navigate.py: gates, the world door, Wizard City's streets), walks up to
the shopkeeper, opens the shop, and sells only names that appeared during
this run. A name shared with an older item, a couch potato, or anything
equipped is not clicked. A sale that touched anything else stops selling for
the run. Then Recall (or a walk) goes back. Nothing teleports.

The shop's windows were never seen live: when a step does not find what it
expects, the shop's window tree is written to the run folder.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from loguru import logger

from . import blackbox
from .garden import is_couch_potato

BAZAAR = "WizardCity/WC_Streets/Interiors/WC_OldeTown_AuctionHouse"
SHOPKEEPER = ("elik", "silverfist", "merchant", "bazaar", "vendor", "shop")

# Category buttons. Clicking one selects a whole class, including old items.
_BULK = {
    "all",
    "select all",
    "sell all",
    "everything",
    "gear",
    "hats",
    "hat",
    "robes",
    "robe",
    "boots",
    "boot",
    "wands",
    "wand",
    "athames",
    "athame",
    "rings",
    "ring",
    "amulets",
    "amulet",
    "decks",
    "deck",
    "reagents",
    "reagent",
    "treasure cards",
    "treasure card",
    "housing",
    "seeds",
    "seed",
    "snacks",
    "snack",
    "jewels",
    "jewel",
    "pets",
    "pet",
    "buy",
    "bank",
    "bank sell",
}


@dataclass(frozen=True)
class BagItem:
    perm_id: int
    name: str


def norm_name(name: str) -> str:
    return " ".join((name or "").replace("_", " ").split()).lower()


def names_to_sell(
    baseline: set[int],
    backpack: list[BagItem],
    equipped_names: set[str],
) -> Counter[str]:
    """How many of each display name are safe to click in the shop.

    A name is safe only when every copy in the bag showed up after the run
    started. One older copy, an unreadable id, a couch potato, or a name
    that is equipped keeps the whole name.
    """
    groups: dict[str, list[BagItem]] = {}
    for item in backpack:
        key = norm_name(item.name)
        if not key:
            continue
        groups.setdefault(key, []).append(item)
    equipped = {norm_name(name) for name in equipped_names}
    sell: Counter[str] = Counter()
    for key, group in groups.items():
        if is_couch_potato(key) or key in equipped:
            continue
        if any(item.perm_id <= 0 or item.perm_id in baseline for item in group):
            continue
        sell[group[0].name] = len(group)
    return sell


def click_is_one_item(text: str, allowed: set[str]) -> bool:
    """A shop row for one farm drop. Category buttons are not rows."""
    key = norm_name(text)
    if not key or key in _BULK or key.startswith("all "):
        return False
    return key in allowed


def is_sell_button(name: str, text: str) -> bool:
    """The button that sells the selection. The Sell tab is not that button."""
    window = (name or "").lower()
    if "tab" in window:
        return False
    label = norm_name(text)
    if label in {"sell item", "sell items", "sell selected", "confirm sale"}:
        return True
    return "button" in window and "sell" in window and "buy" not in window


def sale_kept_the_rest(
    baseline: set[int],
    equipped_before: set[int],
    potatoes_before: int,
    now_ids: set[int],
    equipped_now: set[int],
    potatoes_now: int,
) -> bool:
    """The sale removed nothing that was already owned, worn, or a couch potato."""
    if equipped_now != equipped_before:
        return False
    if potatoes_now < potatoes_before:
        return False
    return baseline <= now_ids


class LootBook:
    """The bag as it was when this run started. Later copies are the farm."""

    def __init__(self):
        self.baseline: set[int] | None = None
        self.selling = True

    def see(self, items: list[BagItem]) -> None:
        if self.baseline is None:
            self.baseline = {item.perm_id for item in items if item.perm_id > 0}


async def _backpack(client) -> list[BagItem] | None:
    try:
        inventory = await client.client_object.try_get_inventory_behavior()
        if inventory is None:
            return None
        items = []
        for item in await inventory.item_list():
            try:
                perm = int(await item.perm_id())
            except Exception:
                perm = 0
            items.append(BagItem(perm, await _label(client, item)))
        return items
    except Exception as exc:
        logger.info(f"seeds: backpack unreadable ({exc})")
        return None


async def _equipped(client) -> tuple[set[int], set[str]]:
    ids: set[int] = set()
    names: set[str] = set()
    try:
        gear = await client.client_object.try_get_equipment_behavior()
        if gear is None:
            return ids, names
        for item in await gear.item_list():
            try:
                perm = int(await item.perm_id())
            except Exception:
                perm = 0
            if perm > 0:
                ids.add(perm)
            label = await _label(client, item)
            if label:
                names.add(label)
    except Exception as exc:
        logger.info(f"seeds: equipped gear unreadable ({exc})")
    return ids, names


async def _label(client, item) -> str:
    try:
        template = await item.object_template()
        if template is None:
            return ""
        from wizwalker.memory.memory_objects.game_object_template import WizGameObjectTemplate

        wiz = WizGameObjectTemplate(client.hook_handler, await template.read_base_address())
        code = (await wiz.display_name() or "").strip()
        cache = getattr(client, "cache_handler", None)
        if code and cache is not None:
            try:
                named = await cache.get_langcode_name(code)
                if named and named.strip() and named.strip() != code:
                    return named.strip()
            except Exception:
                pass
        raw = (await wiz.object_name() or "").replace("_", " ").strip()
        return raw
    except Exception:
        return ""


def _potato_count(items: list[BagItem]) -> int:
    from .garden import potatoes_in

    return sum(potatoes_in(item.name) for item in items)


async def sell_here(client, controller, book: LootBook) -> None:
    """In the Bazaar: open the shop and sell this run's new names."""
    from . import ui
    from .travel import entity_pos

    if book.baseline is None:
        logger.info("sell: the bag was never read; not selling")
        return
    if controller:
        controller.allow_idle(360)
    try:
        before = await _backpack(client)
        equipped_ids, equipped_names = await _equipped(client)
        if before is None:
            return
        potatoes = _potato_count(before)
        gold = await _gold(client)
        wanted = names_to_sell(book.baseline, before, equipped_names)
        if not wanted:
            logger.info("sell: the bag is full of items from before this run; nothing is sold")
            await asyncio.sleep(20.0)
            return
        logger.info(f"sell: selling {sum(wanted.values())} item(s) from this run at the Bazaar")
        blackbox.event("sell", items=dict(wanted))
        if not await _open_shop(client, entity_pos):
            from .navigate import Navigator

            logger.info("sell: the shop did not open")
            await _dump_shop(client)
            await Navigator(client)._dump_entities(BAZAAR)
            return
        if not await ui.click_text(client, "Sell"):
            logger.info("sell: the shop has no Sell tab; leaving the bag alone")
            await _close_shop(client)
            return
        await asyncio.sleep(0.4)
        buddy = await ui.click_text(client, "Backpack Buddy")
        if buddy:
            logger.info("sell: opened Backpack Buddy")
        else:
            logger.info("sell: no Backpack Buddy button; using the sell list")
        await asyncio.sleep(0.6)
        clicked = await _click_loot(client, wanted)
        if clicked == 0:
            await _dump_shop(client)
            logger.info("sell: the farm loot was not on the sell list; nothing was sold")
            await _close_shop(client)
            return
        if not await _click_sell(client):
            await _dump_shop(client)
            logger.info("sell: no sell button for the selection; closing the shop")
            await _close_shop(client)
            return
        await asyncio.sleep(0.4)
        await ui.confirm_modal(client)
        await asyncio.sleep(0.8)
        await _close_shop(client)
        if not await _sale_was_safe(client, book, equipped_ids, potatoes, gold):
            book.selling = False
            logger.info("sell: the sale touched something else; no more selling this run")
    finally:
        if controller:
            controller.end_idle()


async def _open_shop(client, entity_pos) -> bool:
    """Walk up to the shopkeeper and use the prompt until the shop window opens."""
    from . import ui
    from .navigate import Navigator, walk_in_zone

    nav = Navigator(client)
    for _ in range(4):
        if await ui.window_at(client, ["WorldView", "shopGUI"]):
            return True
        place = await entity_pos(client, SHOPKEEPER)
        if place is not None:
            await walk_in_zone(client, (place.x, place.y, place.z), why="to the shopkeeper", reach=120.0)
        await nav.use()
        await asyncio.sleep(1.2)
    return await ui.window_at(client, ["WorldView", "shopGUI"]) is not None


async def _rows(client):
    """Visible windows that have their own caption, not a copy of a child's."""
    from . import ui

    found = []

    async def walk(window, depth: int = 0):
        if depth > 8:
            return []
        texts = []
        try:
            if not await window.is_visible():
                return texts
            name = await window.name() or ""
            text = ui._TAGS.sub("", await window.maybe_text() or "").strip()
            for child in await window.children():
                texts.extend(await walk(child, depth + 1))
            if text and text not in texts:
                found.append((window, name, text))
            if text:
                texts.append(text)
        except Exception:
            return texts
        return texts

    try:
        await walk(client.root_window)
    except Exception:
        pass
    return found


async def _click_loot(client, wanted: Counter[str]) -> int:
    remaining = Counter({norm_name(name): count for name, count in wanted.items()})
    allowed = {key for key, count in remaining.items() if count > 0}
    clicked = 0
    for window, _name, text in await _rows(client):
        key = norm_name(text)
        if remaining.get(key, 0) <= 0 or not click_is_one_item(text, allowed):
            continue
        try:
            await client.mouse_handler.click_window(window)
        except Exception:
            continue
        remaining[key] -= 1
        clicked += 1
        await asyncio.sleep(0.25)
    return clicked


async def _click_sell(client) -> bool:
    for window, name, text in await _rows(client):
        if not is_sell_button(name, text):
            continue
        try:
            await client.mouse_handler.click_window(window)
            return True
        except Exception:
            continue
    return False


async def _sale_was_safe(client, book: LootBook, equipped_before: set[int], potatoes: int, gold) -> bool:
    now = await _backpack(client)
    equipped_now, _names = await _equipped(client)
    if now is None:
        return False
    later = await _gold(client)
    if gold is not None and later is not None and later < gold:
        logger.info("sell: gold went down, so that click bought instead of sold")
        return False
    kept = sale_kept_the_rest(
        book.baseline or set(),
        equipped_before,
        potatoes,
        {item.perm_id for item in now if item.perm_id > 0},
        equipped_now,
        _potato_count(now),
    )
    if kept:
        logger.info("sell: sold this run's loot; couch potatoes and older items stayed")
    return kept


async def _gold(client) -> int | None:
    try:
        return int(await client.stats.current_gold())
    except Exception:
        return None


async def _close_shop(client) -> None:
    from . import ui

    try:
        await ui.close_menus(client)
    except Exception:
        pass


async def _dump_shop(client) -> None:
    from pathlib import Path

    from . import ui

    try:
        lines = await ui.dump_tree(client.root_window, max_depth=7)
        path = (blackbox.folder() or Path("state")) / "bazaar_window.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("sell: wrote the shop windows to state/bazaar_window.txt")
    except Exception as exc:
        logger.info(f"seeds: could not write the shop windows ({exc})")


async def sell_trip(client, controller, book: LootBook, home_zone: str) -> bool:
    """Mark, walk to the Bazaar, sell, Recall back. True when the bag has room again."""
    from .navigate import trip
    from .seeds import bag_is_full

    blackbox.event("sell_trip")

    async def errand() -> None:
        await sell_here(client, controller, book)

    went = await trip(
        client, BAZAAR, home_zone, errand, controller=controller, on_fight=getattr(client, "_on_fight", None)
    )
    if not went:
        logger.info("sell: could not walk to the Bazaar")
        return False
    try:
        used, capacity = await client.backpack_space()
    except Exception:
        return False
    return not bag_is_full(int(used), int(capacity))
