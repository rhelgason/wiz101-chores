"""WizWalker hooks whose bodies are built from the game's own bytes (hookbytes.py).

Shims, not patches: WizWalker's site-packages stay as installed.
"""

from __future__ import annotations

from wizwalker.memory.hooks import ClientHook, RootWindowHook

from .hookbytes import CLIENT_PATTERN, client_body, root_window_body


class AnyBuildClientHook(ClientHook):
    """ClientHook with the stack slot as a wildcard and the replaced load replayed as read."""

    pattern = CLIENT_PATTERN

    async def bytecode_generator(self, packed_exports):
        original = await self.read_bytes(self.jump_address, self.instruction_length)
        return client_body(original, packed_exports[0][1])


class AnyBuildRootWindowHook(RootWindowHook):
    """RootWindowHook that reads the field through whichever base register the build uses."""

    async def bytecode_generator(self, packed_exports):
        original = await self.read_bytes(self.jump_address, self.instruction_length)
        return root_window_body(original, packed_exports[0][1])
