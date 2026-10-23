"""Sell this run's loot from the backpack's Quick Sell, then go back to fighting.

A seeds run reads the backpack when it starts (LootBook). When the bag is
two slots from full, `quick_sell` opens the backpack (B), presses its Quick
Sell button, and ticks only rows whose name appeared during this run: never
a couch potato, anything equipped, or a name shared with an older item. It
sells one page at a time and starts over, since the list shrinks. Nothing
walks and nothing teleports.

When the rows show no names, it may press Select All in the gear view only
if the list holds no more rows than this run's new gear (so every older item
is locked), and the confirmation counts agree. Otherwise it stops and says
so. Window names come from the game's own GUI files (_Shared-WorldData.wad:
InventorySpellbookPage, QuickSell, SellQuickSell, QuickSellConfirmationWindow);
the first time the list opens its window tree is written to the run folder.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass

from loguru import logger

from . import blackbox
from .garden import is_couch_potato

ROWS = 10  # shoplist0..9 (the item) beside num0..9 (its Selected box)
MAX_PAGES = 40
MAX_SALES = 60
VIEWS = ("BackpackStuff", "HousingStuff", "JewelStuff")  # gear first; Select All only there
# The list shows only the ticked category of the view (QuickSell.gui), and the
# game keeps whichever was ticked last. 10-21 03:16: it opened listing one
# treasure card, none of the run's 80 new items was on it, and nothing sold.
CATEGORIES = {
    "BackpackStuff": ("ShopCategory_All",),
    # Not ShopCategory_Seed: the couch potatoes are seeds.
    "HousingStuff": (
        "ShopCategory_PlantLife",
        "ShopCategory_WallHangings",
        "ShopCategory_WallpaperCarpet",
        "ShopCategory_Outdoor",
        "ShopCategory_Furniture",
        "ShopCategory_Decoration",
        "ShopCategory_MusicScroll",
    ),
    "JewelStuff": ("ShopCategory_Jewel",),
}

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
    kind: str = ""  # the template's object type: "equip_head", ... ("" unread)


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
    if not key or key in _BULK or key.startswith("all ") or is_couch_potato(key):
        return False
    return key in allowed


def pick_rows(labels: list[str], remaining: Counter[str]) -> list[int]:
    """Rows on this page to tick, by name. `remaining` (normalized names ->
    how many copies are new this run) is spent as rows are picked."""
    allowed = {key for key, count in remaining.items() if count > 0}
    picked = []
    for row, label in enumerate(labels):
        key = norm_name(label)
        if remaining.get(key, 0) > 0 and click_is_one_item(label, allowed):
            remaining[key] -= 1
            picked.append(row)
    return picked


def new_gear(baseline: set[int], items: list[BagItem]) -> int:
    """Items new this run whose type is a piece of gear."""
    return sum(
        1
        for item in items
        if item.perm_id > 0
        and item.perm_id not in baseline
        and item.kind.startswith("equip")
        and not is_couch_potato(item.name)
    )


def select_all_is_safe(rows: int, gear: int) -> bool:
    """Select All only when the list cannot hold an older item: no more rows
    than gear that dropped this run (older gear has to be locked)."""
    return 0 < rows <= gear


def count_text(text: str) -> int | None:
    found = re.search(r"\d[\d,]*", text or "")
    return int(found.group(0).replace(",", "")) if found else None


def confirmation_ok(total: int | None, pets: int | None, ticked: int, *, by_name: bool) -> bool:
    """The game's own summary agrees with what was ticked. A pet is never sold."""
    if pets:
        return False
    if total is None:
        return by_name  # names were checked row by row; Select All needs the count
    return 0 < total <= ticked


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
        self.dumped = False

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
            items.append(BagItem(perm, await _label(client, item), await _kind(client, item)))
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


async def _kind(client, item) -> str:
    try:
        template = await item.object_template()
        if template is None:
            return ""
        from wizwalker.memory.memory_objects.game_object_template import WizGameObjectTemplate

        wiz = WizGameObjectTemplate(client.hook_handler, await template.read_base_address())
        return (await wiz.object_type()).name
    except Exception:
        return ""


def _potato_count(items: list[BagItem]) -> int:
    from .garden import potatoes_in

    return sum(potatoes_in(item.name) for item in items)


async def quick_sell(client, controller, book: LootBook) -> bool:
    """Sell this run's loot through the backpack's Quick Sell. True when the
    bag has room again."""
    from .seeds import bag_is_full

    if book.baseline is None:
        logger.info("sell: the bag was never read; not selling")
        return False
    if controller:
        controller.allow_idle(300)
    try:
        before = await _backpack(client)
        equipped_ids, equipped_names = await _equipped(client)
        if before is None:
            return False
        wanted = names_to_sell(book.baseline, before, equipped_names)
        gear = new_gear(book.baseline, before)
        if not wanted:
            logger.info("sell: everything in the bag was there before this run; nothing is sold")
            return False
        potatoes = _potato_count(before)
        gold = await _gold(client)
        logger.info(
            f"sell: quick selling {sum(wanted.values())} item(s) from this run "
            f"({len(wanted)} name(s), {gear} piece(s) of gear)"
        )
        blackbox.event("quick_sell", items=dict(wanted), gear=gear)
        if not await _open_quick_sell(client):
            logger.info("sell: Quick Sell did not open; the windows are in quick_sell_window.txt")
            await _dump(client, "quick_sell_window.txt")
            return False
        if not book.dumped:
            book.dumped = True
            await _dump(client, "quick_sell_window.txt")
        remaining = Counter({norm_name(name): count for name, count in wanted.items()})
        sold = 0
        for view in VIEWS:
            sold += await _sell_view(client, view, remaining, gear if view == VIEWS[0] else 0)
            if await _fighting(client):
                logger.info("sell: a fight started; selling again later")
                break
    finally:
        await _close(client)
        if controller:
            controller.end_idle()
    if sold and not await _sale_was_safe(client, book, equipped_ids, potatoes, gold):
        book.selling = False
        logger.warning("sell: the sale touched something else; no more selling this run")
    logger.info(f"sell: {sold} sale(s) made")
    try:
        used, capacity = await client.backpack_space()
    except Exception:
        return False
    return not bag_is_full(int(used), int(capacity))


async def _open_quick_sell(client) -> bool:
    """B opens the backpack; its QuickSell_Item button opens the sell list."""
    from . import ui
    from .walk import VK, keys_for, request_focus

    for attempt in range(3):
        if await _sell_window(client) is not None:
            return True
        if await _visible(client, "InventorySpellbookPage") is None:
            request_focus("opening the backpack")  # B only reaches the active Mac app
            await asyncio.sleep(1.5 if attempt else 0.5)
            await keys_for(client).tap(VK["B"])
            await asyncio.sleep(1.2)
        if await _visible(client, "InventorySpellbookPage") is None:
            await ui.click_named(client, "btnSpellbook")  # the spellbook, if B did not
            await asyncio.sleep(1.0)
        button = await _visible(client, "QuickSell_Item")
        if button is not None:
            await ui.click_center(client, button)
            await asyncio.sleep(1.5)
    return await _sell_window(client) is not None


async def _sell_view(client, view: str, remaining: Counter[str], gear: int) -> int:
    """Sell from one view (gear, housing, jewels), category by category. Returns sales."""
    from . import ui

    if await _sell_window(client) is None:
        return 0
    if view != VIEWS[0]:
        switch = await _visible(client, f"NextCategory_{view}")
        if switch is None:
            logger.info(f"sell: no way to the {view} view")
            return 0
        await ui.click_center(client, switch)
        await asyncio.sleep(0.8)
    sales = 0
    for category in CATEGORIES[view]:
        if not any(count > 0 for count in remaining.values()):
            break
        button = await _visible(client, category)
        if button is None:
            logger.info(f"sell: no {category} in Quick Sell")
            continue
        await ui.click_center(client, button)
        await asyncio.sleep(0.8)
        bulk = gear if category == "ShopCategory_All" else 0
        sales += await _sell_listed(client, category, remaining, bulk)
        if await _fighting(client):
            break
    return sales


async def _sell_listed(client, category: str, remaining: Counter[str], gear: int) -> int:
    """Tick this run's rows in the list as it is shown, page by page, and sell them."""
    sales = 0
    for _ in range(MAX_SALES):
        if await _fighting(client):
            return sales
        window = await _sell_window(client)
        if window is None:
            return sales
        await _first_page(client, window)
        page = await _page_rows(window)
        labels = [label for label, _item, _box in page]
        if page and not any(labels):
            if category != "ShopCategory_All" or sales:
                return sales
            return await _select_all(client, window, gear)
        ticked = 0
        unsure = 0
        seen = 0
        for _page in range(MAX_PAGES):
            if await _fighting(client):
                return sales  # a mob walked in: stop clicking; the fight comes first
            seen += len(page)
            picks = pick_rows([label for label, _i, _b in page], remaining)
            for row in picks:
                state = await _tick(client, page[row][1], page[row][2])
                if state is not False:
                    ticked += 1
                    unsure += state is None
            if ticked or not await _next_page(client, window):
                break
            page = await _page_rows(window)
        logger.info(f"sell: {category}: {seen} row(s) looked at, {ticked} ticked ({unsure} unread)")
        blackbox.event("quick_sell_list", category=category, rows=seen, ticked=ticked, unread=unsure)
        if not ticked:
            return sales
        # A tick whose box could not be read needs the game's own count to agree.
        if not await _sell_selected(client, window, ticked, by_name=not unsure):
            return sales
        sales += 1
    return sales


async def _select_all(client, window, gear: int) -> int:
    """The rows have no readable names: Select All, only if every row must be new."""
    from . import ui

    rows = 0
    for _ in range(MAX_PAGES):
        rows += len(await _page_rows(window))
        if not await _next_page(client, window):
            break
    if not select_all_is_safe(rows, gear):
        logger.warning(
            f"sell: the Quick Sell list shows no item names and holds {rows} row(s), but only {gear} "
            "piece(s) of gear dropped this run. Lock the gear you keep (backpack, Lock) and it can "
            "sell the rest; nothing was sold."
        )
        blackbox.event("quick_sell_refused", rows=rows, gear=gear)
        return 0
    button = await _visible_in(window, "AllAction")
    if button is None:
        return 0
    await ui.click_center(client, button)
    await asyncio.sleep(0.6)
    return 1 if await _sell_selected(client, window, rows, by_name=False) else 0


async def _sell_selected(client, window, ticked: int, *, by_name: bool) -> bool:
    """Press Sell, check the game's summary, press Sell on it, wait it out."""
    from . import ui

    button = await _visible_in(window, "sellAction")
    if button is None:
        logger.info("sell: no Sell button in Quick Sell")
        return False
    await ui.click_center(client, button)
    confirm = None
    for _ in range(12):
        await asyncio.sleep(0.4)
        confirm = await _visible(client, "QuickSellConfirmationWindow")
        if confirm is not None:
            break
    if confirm is None:
        logger.info("sell: the Quick Sell summary did not open")
        return False
    total = count_text(await _text_in(confirm, "TotalNumber"))
    pets = count_text(await _text_in(confirm, "PetNumber"))
    if not confirmation_ok(total, pets, ticked, by_name=by_name):
        logger.warning(f"sell: the summary says {total} item(s), {pets} pet(s); {ticked} ticked; not selling")
        blackbox.event("quick_sell_cancelled", total=total, pets=pets, ticked=ticked)
        close = await _visible_in(confirm, "QuickSellConfirmationCloseButton")
        if close is not None:
            await ui.click_center(client, close)
        return False
    sell = await _visible_in(confirm, "SellButton")
    if sell is None:
        return False
    await ui.click_center(client, sell)
    blackbox.event("quick_sold", total=total, ticked=ticked)
    logger.info(f"sell: sold {total if total is not None else ticked} item(s)")
    for _ in range(40):
        await asyncio.sleep(0.5)
        if await _visible(client, "QuickSellWaitingWindow") is None:
            break
    await asyncio.sleep(0.5)
    await ui.confirm_modal(client)
    return True


async def _page_rows(window) -> list[tuple[str, object, object]]:
    """(name, row, Selected box) for each visible row on this page."""
    from . import ui

    names = {f"shoplist{i}" for i in range(ROWS)} | {f"num{i}" for i in range(ROWS)}
    found = await ui.find_named(window, names)
    rows = []
    for i in range(ROWS):
        item = found.get(f"shoplist{i}")
        if item is None or not await ui.window_visible(item):
            continue
        box = None
        num = found.get(f"num{i}")
        if num is not None:
            box = (await ui.find_named(num, {"Selected"}, max_depth=2)).get("Selected")
        rows.append((await _row_label(item), item, box))
    return rows


async def _row_label(item) -> str:
    """The row's caption: its own text, its tooltip, or a child's text."""
    from . import ui

    text = await ui.window_text(item)
    if text:
        return text
    try:
        tip = ui._TAGS.sub("", await item.tip() or "").strip()
        if tip and not tip.startswith("string;"):
            return tip
    except Exception:
        pass
    try:
        for child in await item.children():
            text = await ui.window_text(child)
            if text:
                return text
    except Exception:
        pass
    return ""


PROBE_SIZE = 0x600  # bytes of a Selected box compared before and after its first click
_probed = False


async def _tick(client, item, box) -> bool | None:
    """Click a row's Selected box once (the row itself when it has none).

    True when the box reads checked afterwards; None when the click went in
    but the state is unknown. wizwalker's `maybe_checked` is the battle
    SpellCheckBox's offset: on 10-23 (run 20261022-190354-seeds) every tick
    read unchecked, the row was then clicked too (likely unticking it), and
    270 rows a pass gave 0 sales for 40 minutes. The box is never clicked twice.
    """
    from . import ui

    target = box if box is not None else item
    try:
        before = await _probe_bytes(box)
        await ui.click_center(client, target)
        await asyncio.sleep(0.25)
        if box is None:
            return None
        await _probe_report(box, before)
        return True if await box.maybe_checked() else None
    except Exception as exc:
        logger.debug(f"sell: tick failed: {exc}")
        return False


async def _probe_bytes(box) -> bytes | None:
    return None if _probed or box is None else await _box_bytes(box)


async def _box_bytes(box) -> bytes | None:
    try:
        return await box.read_bytes(await box.read_base_address(), PROBE_SIZE)
    except Exception:
        return None


async def _probe_report(box, before: bytes | None) -> None:
    """Log which bytes of the box the first click changed (to learn its checked flag)."""
    global _probed
    if before is None or _probed:
        return
    _probed = True
    after = await _box_bytes(box)
    if after is None:
        return
    changed = [(i, before[i], after[i]) for i in range(min(len(before), len(after))) if before[i] != after[i]]
    logger.info(f"sell: the first tick changed {len(changed)} byte(s) of its box: {changed[:16]}")
    blackbox.event("checkbox_probe", changed=changed[:32])


async def _first_page(client, window) -> None:
    from . import ui

    for _ in range(MAX_PAGES):
        before = await _text_in(window, "labelScrollPage")
        left = await _visible_in(window, "leftscroll")
        if left is None or not before or before.split("/")[0].strip() in ("", "1"):
            return
        await ui.click_center(client, left)
        await asyncio.sleep(0.4)
        if await _text_in(window, "labelScrollPage") == before:
            return


async def _next_page(client, window) -> bool:
    """Turn the page. False on the last one, or when the page number cannot be read."""
    from . import ui

    before = await _text_in(window, "labelScrollPage")
    right = await _visible_in(window, "rightscroll")
    if not before or right is None:
        return False
    await ui.click_center(client, right)
    await asyncio.sleep(0.5)
    return (await _text_in(window, "labelScrollPage")) != before


async def _sell_window(client):
    """The Quick Sell list (SellQuickSell.gui's buyWindow with a sellAction)."""
    from . import ui

    try:
        for window in await client.root_window.get_windows_with_name("buyWindow"):
            if await ui.window_visible(window) and (await ui.find_named(window, {"sellAction"}, max_depth=2)):
                return window
    except Exception:
        pass
    return None


async def _visible(client, name: str):
    from . import ui

    return await ui._visible_named(client.root_window, name)


async def _visible_in(window, name: str):
    from . import ui

    try:
        for found in await window.get_windows_with_name(name):
            if await ui.window_visible(found):
                return found
    except Exception:
        pass
    return None


async def _text_in(window, name: str) -> str:
    from . import ui

    found = await _visible_in(window, name)
    return await ui.window_text(found)


async def _fighting(client) -> bool:
    try:
        return bool(await client.in_battle())
    except Exception:
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


async def _close(client) -> None:
    """Close the sell list and the backpack."""
    from . import ui
    from .walk import VK, keys_for

    try:
        window = await _sell_window(client)
        if window is not None:
            exit_button = await _visible_in(window, "exit")
            if exit_button is not None:
                await ui.click_center(client, exit_button)
                await asyncio.sleep(0.4)
        await ui.close_menus(client)
        await asyncio.sleep(0.4)
        if await _visible(client, "InventorySpellbookPage") is not None:
            await keys_for(client).tap(VK["B"])
    except Exception:
        pass


async def _dump(client, filename: str) -> None:
    from pathlib import Path

    from . import ui

    try:
        lines = await ui.dump_tree(client.root_window, max_depth=9, with_types=True)
        path = (blackbox.folder() or Path("state")) / filename
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"sell: wrote the windows to {path}")
    except Exception as exc:
        logger.info(f"sell: could not write the windows ({exc})")
