"""Read the garden and cast a spell only when a plant is asking for it.

Rank and seed counts come from the wizard. A spell click happens only when
the plant's own text names the need and a visible spell caption matches it.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from loguru import logger

from . import ui

GARDEN_FILE = Path("state") / "garden.json"
DUMP = Path("state") / "garden_windows.txt"

# A short caption on the plant, then the word a matching spell must contain.
NEEDS = (
    ("needs water", "water"),
    ("needs sunlight", "sun"),
    ("needs sun", "sun"),
    ("needs pollination", "pollinat"),
    ("needs magic", "magic"),
    ("needs music", "music"),
    ("pest", "pest"),
)


def garden_state() -> dict:
    """Rank, barn, and seeds. A missing file is a new gardener."""
    base = {
        "rank": 1,
        "red_barn": False,
        "couch_potato_seeds": 0,
        "couch_potatoes": 0,
        "boom_shroom_seeds": 0,
        "plants_need_care": False,
    }
    try:
        data = json.loads(GARDEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if isinstance(data, dict):
        base.update({k: data[k] for k in base if k in data})
    return base


def save_garden(state: dict) -> None:
    try:
        GARDEN_FILE.parent.mkdir(exist_ok=True)
        GARDEN_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.debug(f"garden: could not save ({exc})")


def count_seeds(names) -> tuple[int, int]:
    """Couch-potato seeds, then boom-shroom seeds. Snacks are not seeds."""
    couch = boom = 0
    for name in names:
        low = name.lower().replace("_", " ")
        if "seed" not in low:
            continue
        if "couch potato" in low or "couchpotato" in low:
            couch += 1
        elif "boom shroom" in low or "boomshroom" in low:
            boom += 1
    return couch, boom


_STACK = re.compile(r"(?:x|×)\s*(\d+)\s*$", re.I)


def is_couch_potato(name: str) -> bool:
    """A couch potato seed or a harvested couch potato. Not a boom shroom."""
    low = (name or "").lower().replace("_", " ")
    return "couch potato" in low or "couchpotato" in low.replace(" ", "")


def potatoes_in(name: str, quantity: int | None = None) -> int:
    """How many couch potatoes this one backpack entry is. Zero for anything else."""
    if not is_couch_potato(name):
        return 0
    if quantity is not None and quantity > 0:
        return int(quantity)
    match = _STACK.search((name or "").strip())
    if match:
        return int(match.group(1))
    return 1


def count_potatoes(items) -> int:
    """Couch potatoes in the bag. An entry is a name, or (name, stack size).

    Seeds and snacks both count. A stack size is used when the caller has one.
    """
    total = 0
    for item in items:
        if isinstance(item, tuple):
            total += potatoes_in(item[0], item[1] if len(item) > 1 else None)
        else:
            total += potatoes_in(str(item))
    return total


def loot_mentioned_potato(texts) -> bool:
    """A short reward line naming a couch potato seed. Quest text does not count."""
    for text in texts:
        low = (text or "").lower().replace("_", " ")
        if "couch potato" not in low or "seed" not in low or len(text) > 60:
            continue
        if any(word in low for word in ("collect", "quest", "objective")):
            continue
        return True
    return False


def need_spell(texts) -> str | None:
    """The spell word a plant is asking for, or None when no need is written."""
    blob = " ".join(texts).lower()
    for phrase, spell in NEEDS:
        if phrase in blob:
            return spell
    return None


def spell_caption(texts, spell: str) -> str | None:
    """The visible spell caption to click. The plant's own "needs ..." line is not a button."""
    for text in texts:
        low = text.lower()
        if spell not in low or len(text) > 40 or "need" in low:
            continue
        return text.strip()
    return None


async def note_live(client) -> dict:
    """Refresh rank and seed counts from the wizard. Travel is left to tend."""
    state = garden_state()
    rank = await _rank(client)
    if rank is not None:
        state["rank"] = rank
    stacks = await _item_stacks(client)
    if stacks is not None:
        names = [name for name, _qty in stacks]
        couch, boom = count_seeds(names)
        state["couch_potato_seeds"] = couch
        state["boom_shroom_seeds"] = boom
        # A stack that reads as one slot must not wipe a higher count already seen.
        counted = count_potatoes(stacks)
        state["couch_potatoes"] = max(counted, int(state.get("couch_potatoes") or 0))
    save_garden(state)
    return state


async def tend(client, controller=None, why: str = "") -> bool:
    """Go home, read the plants, and cast at most one spell per plant."""
    if controller:
        controller.allow_idle(180)
    try:
        bought = False
        if "buy boom" in why.lower() or "farley" in why.lower():
            bought = await _buy_boom_seed(client)
        from .travel import go_home

        if not await go_home(client):
            logger.info("garden: could not go home")
            return bought
        state = await note_live(client)
        plants = await _plant_spots(client)
        logger.info(
            f"garden: rank {state['rank']}, "
            f"{state['boom_shroom_seeds']} boom shroom seeds, "
            f"{state['couch_potato_seeds']} couch potato seeds, "
            f"{len(plants)} plants in the house"
        )
        still_needy = False
        cast = 0
        for pos in plants[:8]:
            if controller:
                await controller.checkpoint()
            try:
                await client.teleport(pos)
            except Exception as exc:
                logger.debug(f"garden: could not reach a plant ({exc})")
                continue
            await asyncio.sleep(0.4)
            await client.send_key(_x(), 0.1)
            await asyncio.sleep(0.6)
            texts = await ui.visible_texts(client)
            spell = need_spell(texts)
            if not spell:
                continue
            caption = spell_caption(texts, spell)
            if not caption:
                still_needy = True
                await _dump(texts)
                logger.info(f"garden: a plant needs {spell}, and that spell is not on screen")
                continue
            if await ui.click_text(client, caption):
                cast += 1
                logger.success(f"garden: cast {caption}")
                await asyncio.sleep(1.0)
            else:
                still_needy = True
        state = garden_state()
        state["plants_need_care"] = still_needy
        save_garden(state)
        return cast > 0 or bought
    finally:
        if controller:
            controller.end_idle()


def _x():
    from wizwalker import Keycode

    return Keycode.X


async def _rank(client) -> int | None:
    try:
        level = int(await client.stats.gardening_level())
    except Exception:
        return None
    if level <= 0:
        return 1
    return min(level, 15)


async def _item_stacks(client) -> list[tuple[str, int | None]] | None:
    """Backpack entries as (object name, stack size). None when the bag cannot be read.

    Stack size is set only when a behavior is named as a stack or a quantity.
    Otherwise the entry counts as one, and a later reward line can add the rest.
    """
    try:
        inventory = await client.client_object.try_get_inventory_behavior()
        if inventory is None:
            return None
        stacks = []
        for item in await inventory.item_list():
            template = await item.object_template()
            name = (await template.object_name() or "") if template else ""
            stacks.append((name, await _stack_size(item)))
        return stacks
    except Exception as exc:
        logger.debug(f"garden: backpack unreadable ({exc})")
        return None


async def _stack_size(item) -> int | None:
    try:
        behaviors = await item.inactive_behaviors()
    except Exception:
        return None
    from wizwalker.constants import Primitive

    for behavior in behaviors:
        try:
            bname = (await behavior.behavior_name() or "").lower()
        except Exception:
            continue
        if "stack" not in bname and "quantity" not in bname:
            continue
        for offset in (112, 120, 128, 132, 72):
            try:
                value = int(await behavior.read_value_from_offset(offset, Primitive.uint32))
            except Exception:
                continue
            if 1 <= value <= 999:
                return value
    return None


async def _plant_spots(client) -> list:
    spots = []
    try:
        entities = await client.get_base_entity_list()
    except Exception:
        return spots
    for entity in entities:
        try:
            template = await entity.object_template()
            name = ((await template.object_name() or "") if template else "").lower()
            if "boom" in name or "couch potato" in name or "couchpotato" in name:
                if "seed" in name:
                    continue
                spots.append(await entity.location())
        except Exception:
            continue
    return spots


async def _buy_boom_seed(client) -> bool:
    """One boom shroom seed from Farley, and only from a button that is not crowns."""
    from .travel import go_to_zone
    from .travel_data import _data, named_zone

    try:
        display = _data()[1]
    except Exception as exc:
        logger.info(f"garden: no zone list for Farley ({exc})")
        return False
    dest = named_zone("Golem Court", display)
    if not dest or not await go_to_zone(client, dest):
        logger.info("garden: could not reach Golem Court for boom shroom seeds")
        return False
    pos = await _entity(client, ("farley",))
    if pos is None:
        logger.info("garden: Farley is not in this zone")
        return False
    await client.teleport(pos)
    await asyncio.sleep(0.5)
    await client.send_key(_x(), 0.1)
    await asyncio.sleep(1.0)
    texts = await ui.visible_texts(client)
    seed = spell_caption(texts, "boom shroom")
    if not seed or "crown" in seed.lower():
        await _dump(texts)
        logger.info("garden: Farley's boom shroom seed button was not on screen")
        return False
    if not await ui.click_text(client, seed):
        return False
    await asyncio.sleep(0.4)
    for label in ("Buy", "buy"):
        if await ui.click_text(client, label):
            logger.info("garden: bought one boom shroom seed")
            return True
    logger.info("garden: the seed was selected and Buy was not on screen")
    return False


async def _entity(client, needles: tuple[str, ...]):
    from .travel import entity_pos

    return await entity_pos(client, needles)


async def _dump(texts: list[str]):
    try:
        DUMP.parent.mkdir(exist_ok=True)
        DUMP.write_text("\n".join(texts), encoding="utf-8")
    except OSError:
        pass
