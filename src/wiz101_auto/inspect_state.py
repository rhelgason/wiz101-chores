"""One-shot look at what the bot can see. The bot must be stopped (one hook)."""

from __future__ import annotations

from loguru import logger

from . import ui
from .bot import close_handler, connect, new_handler
from .collect import is_collectable


async def inspect(show_windows: bool = False):
    handler = new_handler()
    try:
        client = await connect(handler)
        zone = await client.zone_name()
        pos = await client.body.position()
        print(f"zone: {zone}")
        print(f"position: ({pos.x:.0f}, {pos.y:.0f}, {pos.z:.0f})")
        try:
            energy = await client.stats.energy_max()
            print(f"energy max: {energy}")
        except Exception as exc:
            logger.debug(f"energy not read: {exc!r}")
        names = []
        try:
            for entity in await client.get_base_entity_list():
                template = await entity.object_template()
                name = await template.object_name() if template else ""
                if is_collectable(name or ""):
                    names.append(name)
        except Exception as exc:
            logger.debug(f"entity scan failed: {exc!r}")
        print(f"reagents and chests nearby: {len(names)}")
        for name in sorted(set(names))[:30]:
            print(f"  {name}")
        if show_windows:
            print("\n".join(await ui.dump_tree(client.root_window)))
    finally:
        await close_handler(handler)
