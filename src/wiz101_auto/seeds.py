"""Savarstaad seed farm. One mode, for hours: fight, rest, sell the new loot.

Troubled Warriors and Splithoof Barbarians in Savarstaad Pass. A death
drain heals when health is low. Low mana walks back to Northguard, stands
on a mana wisp, and waits. A full backpack goes to Elik Silverfist at the
Wizard City Bazaar, presses X, and uses Backpack Buddy.

Only items that appeared during this run are sold. A name that was already
in the bag is left there, because the shop row does not say which copy is
the new one. Couch potato seeds are kept. Equipped gear is never clicked.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from loguru import logger

from .garden import is_couch_potato

SAVARSTAAD = "Grizzleheim/GH_Hero"
NORTHGUARD = "Grizzleheim/GH_MainHub"
BAZAAR = "WizardCity/WC_Streets/Interiors/WC_OldeTown_AuctionHouse"
MANA_LOW = 0.30  # leave the fight zone below this share of the blue globe
MANA_READY = 0.55  # come back once mana has recovered to here
MANA_WAIT = 180.0  # seconds to stand in Northguard before fighting again anyway
BAG_FREE = 2  # sell while at least this many slots remain, so a drop still fits

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


def mana_is_low(ratio: float | None) -> bool:
    """Unread mana does not send the wizard home."""
    return ratio is not None and ratio < MANA_LOW


def mana_is_ready(ratio: float | None) -> bool:
    return ratio is None or ratio >= MANA_READY


def bag_is_full(used: int | None, capacity: int | None) -> bool:
    """True when a couple of slots are all that remain. Unread space is not full."""
    if used is None or capacity is None or capacity <= 0:
        return False
    return used >= max(capacity - BAG_FREE, 1)


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


async def seed_loop(client, controller, task: dict) -> None:
    """Fight in Savarstaad until the wizard is stopped."""
    from wizwalker.errors import ClientClosedError

    from .safety import BotStopped
    from .world import is_free

    book = LootBook()
    closed = 0
    held = ""
    logger.info(
        "seeds: Savarstaad Pass, Troubled Warriors and Splithoof Barbarians. "
        "Low mana rests in Northguard. A full bag sells only this run's loot "
        "at the Bazaar, and keeps couch potato seeds"
    )
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            from .farm import in_fight, play_current_fight

            if await in_fight(client):
                held = ""
                task["name"] = "seeds"
                await play_current_fight(client, controller)
                closed = 0
                continue
            if not await is_free(client):
                task["name"] = "waiting (dialogue or loading)"
                if held != "busy":
                    logger.info("seeds: in dialogue or a loading screen; waiting")
                    held = "busy"
                await asyncio.sleep(0.4)
                continue
            held = ""
            await _one_pass(client, controller, task, book)
            closed = 0
        except BotStopped:
            break
        except ClientClosedError:
            closed += 1
            from .farm import closed_detail

            detail = closed_detail()
            if closed >= 3:
                logger.error(
                    "Wizard101 closed while the bot was hooked"
                    f"{detail}. Fully quit Wizard101 "
                    "(exit to desktop), start it, log in, stand in the world, then run "
                    "./mac/run.sh again."
                )
                controller.stop("the game client closed")
                break
            if closed == 1:
                logger.warning(f"the game client closed{detail}; retrying")
            task["name"] = "waiting for the game"
            await asyncio.sleep(2.0)
        except Exception as exc:
            closed = 0
            logger.opt(exception=exc).error(f"seed step failed: {exc}")
            task["name"] = "error, retrying"
            await asyncio.sleep(1.0)


async def _one_pass(client, controller, task: dict, book: LootBook) -> None:
    from .farm import fight_one

    backpack = await _backpack(client)
    if backpack is not None:
        book.see(backpack)
    used, capacity = await _space(client)
    if bag_is_full(used, capacity):
        task["name"] = "sell"
        if book.selling:
            await _sell(client, controller, book)
        else:
            logger.info("seeds: the bag is full and selling is stopped for this run")
            await asyncio.sleep(30.0)
        return
    if mana_is_low(await _mana(client)):
        task["name"] = "mana"
        await _rest_mana(client, controller)
        return
    task["name"] = "seeds"
    await fight_one(client, "seeds", controller)


async def _mana(client) -> float | None:
    try:
        current = int(await client.stats.current_mana())
        maximum = int(await client.stats.max_mana())
    except Exception:
        return None
    if maximum <= 0:
        return None
    return current / maximum


async def _space(client) -> tuple[int | None, int | None]:
    try:
        used, capacity = await client.backpack_space()
        return int(used), int(capacity)
    except Exception as exc:
        logger.info(f"seeds: backpack size unreadable ({exc})")
        return None, None


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


async def _rest_mana(client, controller) -> None:
    from .farm import _wisp
    from .travel import go_to_zone

    if controller:
        controller.allow_idle(MANA_WAIT + 60)
    try:
        logger.info("seeds: mana is low; resting in Northguard")
        if not await go_to_zone(client, NORTHGUARD):
            logger.info("seeds: could not reach Northguard; fighting on")
            return
        finder = getattr(client, "find_closest_mana_wisp", None)
        if finder is not None:
            try:
                wisp = await finder(only_safe=False)
            except Exception:
                wisp = None
            if wisp is not None:
                try:
                    await client.teleport(await wisp.location())
                    await asyncio.sleep(0.8)
                    logger.info("seeds: stood on a mana wisp")
                except Exception as exc:
                    logger.info(f"seeds: could not reach a mana wisp ({exc})")
        else:
            await _wisp(client)
        deadline = asyncio.get_running_loop().time() + MANA_WAIT
        while asyncio.get_running_loop().time() < deadline:
            if controller:
                await controller.checkpoint()
            if mana_is_ready(await _mana(client)):
                logger.info("seeds: mana is back; returning to Savarstaad")
                return
            await asyncio.sleep(10.0)
        logger.info("seeds: mana is still low after the rest; trying one fight")
    finally:
        if controller:
            controller.end_idle()


async def _sell(client, controller, book: LootBook) -> None:
    from . import ui
    from .travel import entity_pos, go_to_zone

    if book.baseline is None:
        logger.info("seeds: the bag was never read; not selling")
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
            logger.info("seeds: the bag is full of items from before this run; nothing is sold")
            await asyncio.sleep(20.0)
            return
        logger.info(f"seeds: selling {sum(wanted.values())} item(s) from this run at the Bazaar")
        if not await go_to_zone(client, BAZAAR):
            logger.info("seeds: could not reach the Bazaar")
            return
        if not await _open_shop(client, entity_pos):
            logger.info("seeds: Elik's shop did not open")
            return
        if not await ui.click_text(client, "Sell"):
            logger.info("seeds: the shop has no Sell tab; leaving the bag alone")
            await _close_shop(client)
            return
        await asyncio.sleep(0.4)
        buddy = await ui.click_text(client, "Backpack Buddy")
        if buddy:
            logger.info("seeds: opened Backpack Buddy")
        else:
            logger.info("seeds: no Backpack Buddy button; using the sell list")
        await asyncio.sleep(0.6)
        clicked = await _click_loot(client, wanted)
        if clicked == 0:
            await _dump_shop(client)
            logger.info("seeds: the farm loot was not on the sell list; nothing was sold")
            await _close_shop(client)
            return
        if not await _click_sell(client):
            await _dump_shop(client)
            logger.info("seeds: no sell button for the selection; closing the shop")
            await _close_shop(client)
            return
        await asyncio.sleep(0.4)
        await ui.confirm_modal(client)
        await asyncio.sleep(0.8)
        await _close_shop(client)
        if not await _sale_was_safe(client, book, equipped_ids, potatoes, gold):
            book.selling = False
            logger.info("seeds: the sale touched something else; no more selling this run")
    finally:
        if controller:
            controller.end_idle()


async def _open_shop(client, entity_pos) -> bool:
    from . import ui

    for _ in range(4):
        if await ui.window_at(client, ["WorldView", "shopGUI"]):
            return True
        place = await entity_pos(client, ("elik", "silverfist", "merchant"))
        if place is not None:
            await client.teleport(place)
            await asyncio.sleep(0.5)
        await ui.press_interact(client, keys=True)
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
        logger.info("seeds: gold went down, so that click bought instead of sold")
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
        logger.info("seeds: sold this run's loot; couch potatoes and older items stayed")
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
        path = Path("state") / "bazaar_window.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("seeds: wrote the shop windows to state/bazaar_window.txt")
    except Exception as exc:
        logger.info(f"seeds: could not write the shop windows ({exc})")
