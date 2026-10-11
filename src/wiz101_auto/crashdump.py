"""Read a Wizard101 minidump and say whether the bot's hooks were involved.

The game writes `WizardGraphicalClient_<revision>.dmp` next to the exe when
it crashes, and the next crash overwrites it. `mac/crashwatch.py` copies it
into the run folder and calls `summarize` here. Standard library only: this
runs under macOS Python, not the bottle.

The 2026-10-07 10:55 dump was an execute fault (access violation, flag 8)
at WizardGraphicalClient.exe+0x179f150, the first byte of WizWalker's hook
cave. A same-process rehook had made that page non-executable while the old
hooks still jumped into it. `classify` names that kind of crash.
"""

from __future__ import annotations

import datetime
import struct
from dataclasses import dataclass, field

SIGNATURE = 0x504D444D  # "MDMP"
MODULE_LIST = 4
MEMORY_LIST = 5
EXCEPTION = 6
CONTEXT_REGS = 0x78  # x64 CONTEXT: Rax..R15 then Rip
REGISTERS = "rax rcx rdx rbx rsp rbp rsi rdi r8 r9 r10 r11 r12 r13 r14 r15 rip".split()

CODES = {
    0xC0000005: "access violation",
    0xC000001D: "illegal instruction",
    0xC0000096: "privileged instruction",
    0xC00000FD: "stack overflow",
    0xC0000409: "stack buffer overrun (fail fast)",
    0x80000003: "breakpoint",
    0xC0000374: "heap corruption",
}
ACCESS = {0: "read", 1: "write", 8: "execute"}
# First bytes of WizWalker's hook bodies (wizwalker/memory/hooks.py and the dance hook).
SHELLCODE = {
    bytes.fromhex("518b88740400"): "player hook",
    bytes.fromhex("504889c848a3"): "player stat hook",
    bytes.fromhex("50498d87fc0c"): "quest hook",
    bytes.fromhex("50488bc748a3"): "client hook",
    bytes.fromhex("50498b85d800"): "root window hook",
    bytes.fromhex("504889d848a3"): "render context hook",
    bytes.fromhex("5048a1"): "movement teleport or mouse hook",
    bytes.fromhex("50a0"): "drops toggle hook",
    bytes.fromhex("488bd8488b00"): "dance game hook",
}


def shellcode_at(code_bytes: bytes) -> str:
    """The WizWalker hook whose body starts at the faulting instruction, or ''."""
    at_rip = code_bytes[16:]
    for start, name in sorted(SHELLCODE.items(), key=lambda kv: -len(kv[0])):
        if at_rip.startswith(start):
            return name
    return ""


@dataclass
class Module:
    base: int
    size: int
    path: str

    @property
    def name(self) -> str:
        return self.path.replace("/", "\\").split("\\")[-1]


@dataclass
class Dump:
    time: datetime.datetime | None = None
    thread: int = 0
    code: int = 0
    address: int = 0
    params: list[int] = field(default_factory=list)
    registers: dict[str, int] = field(default_factory=dict)
    modules: list[Module] = field(default_factory=list)
    code_bytes: bytes = b""
    stack_returns: list[int] = field(default_factory=list)

    def where(self, address: int) -> str:
        for m in self.modules:
            if m.base <= address < m.base + m.size:
                return f"{m.name}+{address - m.base:#x}"
        return "outside any module"

    def module_of(self, address: int) -> Module | None:
        for m in self.modules:
            if m.base <= address < m.base + m.size:
                return m
        return None


def _string(data: bytes, rva: int) -> str:
    (n,) = struct.unpack_from("<I", data, rva)
    return data[rva + 4 : rva + 4 + n].decode("utf-16le", "replace")


def parse(data: bytes) -> Dump:
    """The exception, registers, modules, and return addresses on the stack."""
    sig, _ver, count, rva, _sum, stamp, _flags = struct.unpack_from("<IIIIIIQ", data, 0)
    if sig != SIGNATURE:
        raise ValueError("not a minidump")
    dump = Dump()
    if stamp:
        dump.time = datetime.datetime.fromtimestamp(stamp)
    streams: dict[int, tuple[int, int]] = {}
    for i in range(count):
        kind, size, where = struct.unpack_from("<III", data, rva + 12 * i)
        streams.setdefault(kind, (size, where))
    if MODULE_LIST in streams:
        _size, where = streams[MODULE_LIST]
        (n,) = struct.unpack_from("<I", data, where)
        for i in range(n):
            base, size, _check, _stamp, name_rva = struct.unpack_from("<QIIII", data, where + 4 + 108 * i)
            dump.modules.append(Module(base, size, _string(data, name_rva)))
    memory: list[tuple[int, int, int]] = []
    if MEMORY_LIST in streams:
        _size, where = streams[MEMORY_LIST]
        (n,) = struct.unpack_from("<I", data, where)
        for i in range(n):
            start, size, at = struct.unpack_from("<QII", data, where + 4 + 16 * i)
            memory.append((start, size, at))

    def read(address: int, n: int, *, partial: bool = False) -> bytes:
        """n bytes at address; with partial, as many of them as the dump holds."""
        for start, size, at in memory:
            if start <= address < start + size:
                if address + n > start + size and not partial:
                    return b""
                end = min(address + n, start + size)
                return data[at + address - start : at + end - start]
        return b""

    if EXCEPTION in streams:
        _size, where = streams[EXCEPTION]
        dump.thread, _align = struct.unpack_from("<II", data, where)
        dump.code, _flags, _record, dump.address, nparams = struct.unpack_from("<IIQQI", data, where + 8)
        dump.params = list(struct.unpack_from("<15Q", data, where + 8 + 32)[: min(nparams, 15)])
        ctx_size, ctx_rva = struct.unpack_from("<II", data, where + 8 + 152)
        context = data[ctx_rva : ctx_rva + ctx_size]
        if len(context) >= CONTEXT_REGS + 8 * len(REGISTERS):
            values = struct.unpack_from(f"<{len(REGISTERS)}Q", context, CONTEXT_REGS)
            dump.registers = dict(zip(REGISTERS, values, strict=True))
        rip = dump.registers.get("rip", dump.address)
        dump.code_bytes = read(rip - 16, 48)
        stack = read(dump.registers["rsp"], 0x400, partial=True) if dump.registers.get("rsp") else b""
        for off in range(0, len(stack) - 7, 8):
            (value,) = struct.unpack_from("<Q", stack, off)
            if dump.module_of(value) is not None:
                dump.stack_returns.append(value)
    return dump


def describe_exception(dump: Dump) -> str:
    name = CODES.get(dump.code, "exception")
    text = f"{name} ({dump.code:#010x}) at {dump.address:#x} ({dump.where(dump.address)})"
    if dump.code == 0xC0000005 and len(dump.params) >= 2:
        how = ACCESS.get(dump.params[0], f"type {dump.params[0]}")
        text += f": {how} of {dump.params[1]:#x} ({dump.where(dump.params[1])})"
    return text


def hook_ranges(hooks: dict | None) -> list[tuple[str, int, int]]:
    """(label, start, end) for every place the bot wrote code, from hook_session.json."""
    out: list[tuple[str, int, int]] = []
    if not hooks:
        return out
    cave = hooks.get("cave") or {}
    if cave.get("address"):
        out.append(
            (
                "WizWalker hook cave",
                int(cave["address"]),
                int(cave["address"]) + int(cave.get("size") or 4100),
            )
        )
    for name, h in (hooks.get("hooks") or {}).items():
        jump = int(h.get("jump") or 0)
        if jump:
            out.append(
                (f"{name} hook jump site", jump, jump + max(len(bytes.fromhex(h.get("jump_bytes") or "")), 5))
            )
        hook = int(h.get("hook") or 0)
        if hook:
            out.append(
                (f"{name} hook code", hook, hook + max(len(bytes.fromhex(h.get("hook_bytes") or "")), 1))
            )
    for label, start, size in hooks.get("extra_writes") or []:
        out.append((str(label), int(start), int(start) + int(size)))
    return out


def classify(dump: Dump, hooks: dict | None = None) -> str:
    """One line: was this the bot's code, Wine, or the game itself?"""
    spots = hook_ranges(hooks)
    rip = dump.registers.get("rip", dump.address)
    body = shellcode_at(dump.code_bytes)
    if body:
        spots.append((f"{body} body", rip, rip + 1))

    def hit(address: int) -> str:
        for label, start, end in spots:
            if start - 16 <= address < end + 16:
                return label
        return ""

    if dump.code == 0xC0000005 and dump.params[:1] == [8]:
        label = hit(dump.address)
        if label:
            return (
                f"BOT: the game tried to run code in the {label} and that page was not executable. "
                "Hook bytes, or their memory protection, were changed while the game was using them."
            )
        return (
            "The game jumped to memory that is not executable. "
            "Look at the hook ranges and recent code writes."
        )
    label = hit(rip) or hit(dump.address)
    if label:
        return f"BOT: the fault is inside the {label}. A hook's code or a patched instruction crashed."
    for value in dump.stack_returns[:8]:
        label = hit(value)
        if label:
            return f"BOT (likely): a return address on the stack is inside the {label}."
    module = dump.module_of(rip)
    if module is None:
        return "The fault is outside every module (a bad jump or freed code)."
    low = module.name.lower()
    if low in {"wined3d.dll", "d3d9.dll", "opengl32.dll", "winemac.drv", "libvkd3d-1.dll", "dxgi.dll"}:
        return f"Graphics: the fault is in {module.name} (Wine's Direct3D layer), not in hook code."
    if low.startswith("wizardgraphicalclient"):
        return "Game code faulted outside every hook. Compare with recent memory writes in events.jsonl."
    return f"The fault is in {module.name}."


def summarize(data: bytes, hooks: dict | None = None) -> str:
    dump = parse(data)
    lines = [
        f"dump time: {dump.time}",
        f"thread: {dump.thread}",
        f"exception: {describe_exception(dump)}",
        f"verdict: {classify(dump, hooks)}",
    ]
    if dump.registers:
        rip = dump.registers.get("rip", 0)
        lines.append(f"rip: {rip:#x} ({dump.where(rip)})")
    if dump.code_bytes:
        lines.append(f"code at rip-16: {dump.code_bytes.hex()}")
    if dump.stack_returns:
        lines.append("stack (module addresses, innermost first):")
        lines += [f"  {v:#x} {dump.where(v)}" for v in dump.stack_returns[:16]]
    return "\n".join(lines)
