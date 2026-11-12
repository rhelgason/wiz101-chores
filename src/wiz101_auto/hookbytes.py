"""Hook bodies built from the game instruction they replace (no wizwalker import).

11-12: a Wizard101 update (a new world) rebuilt the game. WizWalker's hooks
carry hand-written copies of the instruction each one replaces, and its
patterns for three spots no longer matched:

- ClientHook: same code, one stack slot moved ([rsp+0x38] -> [rsp+0x40]).
- RootWindowHook: the pattern still matched, but the function's base
  register moved from r13 to r15: `mov rcx,[r13+0xD8]` became
  `mov rcx,[r15+0xD8]`. WizWalker's body would have replayed the r13 load and
  handed the game a wrong rcx (a crash, most likely).
- The dance game hook: `mov rbx,rax` became `mov rdi,rax`.

So these bodies are built from the bytes read at the spot, after checking
they are the expected kind of instruction. A spot that is not refuses to
hook (HookShapeError) rather than writing something that could crash.

Every check reads only the instruction: `mov r64, [base + disp]` is position
independent (no RIP-relative form, no SIB byte), so replaying it from the
cave runs the same as in place.
"""

from __future__ import annotations

# ClientHook: the old pattern, with the stack slot and the two short jumps as
# wildcards. One match in the 10-2026 build and one in the 11-12 build.
CLIENT_PATTERN = (
    rb"\x18\x48......\x48\x8B\x7C\x24.\x48\x85\xFF\x74.\x8B\xC6\xF0\x0F\xC1\x47\x08\x83\xF8\x01\x75."
    rb"\x48\x8B\x07\x48\x8B\xCF\xFF\x50\x08\xF0\x0F\xC1\x77\x0C"
)
# Dance game: `mov <rcx|rdx|rbx|rbp|rdi>, rax`, then `cmp [rax+0x10], rsi; jbe; mov eax, esi`.
DANCE_PATTERN = rb"\x48\x8B[\xC8\xD0\xD8\xE8\xF8]\x48\x39\x70\x10\x76.\x8B\xC6"

RAX, RCX, RBX, RSP, RSI, RDI = 0, 1, 3, 4, 6, 7
PUSH_RAX, POP_RAX = b"\x50", b"\x58"


class HookShapeError(RuntimeError):
    """The instruction at a hook spot is not the kind the hook was written for."""


def _mov_load(ins: bytes, what: str) -> tuple[int, int, int]:
    """`mov r64, [base + disp8/32]` -> (rex, reg, rm). Anything else raises."""
    if len(ins) < 4 or not 0x48 <= ins[0] <= 0x4F or ins[1] != 0x8B:
        raise HookShapeError(f"{what}: not a 64-bit load ({ins.hex(' ')})")
    rex, modrm = ins[0], ins[2]
    mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
    if mod not in (1, 2) or rm == RSP:  # mod 0 rm 5 is RIP-relative; rm 4 is a SIB byte
        raise HookShapeError(f"{what}: not a [base + offset] load ({ins.hex(' ')})")
    if len(ins) != 3 + (1 if mod == 1 else 4):
        raise HookShapeError(f"{what}: unexpected length {len(ins)} ({ins.hex(' ')})")
    return rex, reg | (8 if rex & 4 else 0), rm | (8 if rex & 1 else 0)


def client_body(original: bytes, export: bytes) -> bytes:
    """ClientHook: copy rdi (the client object) to the export, then the replaced load."""
    _rex, reg, base = _mov_load(original, "client hook")
    if reg == RDI or base == RDI:
        raise HookShapeError(f"client hook: the replaced load uses rdi ({original.hex(' ')})")
    return (
        PUSH_RAX
        + b"\x48\x8B\xC7"  # mov rax, rdi
        + b"\x48\xA3" + export  # mov [export], rax
        + POP_RAX
        + original
    )


def root_window_body(original: bytes, export: bytes) -> bytes:
    """RootWindowHook: the replaced load is `mov rcx, [base + off]`; load the same
    field into rax for the export, then replay it."""
    rex, reg, base = _mov_load(original, "root window hook")
    if reg != RCX:
        raise HookShapeError(f"root window hook: the load is not into rcx ({original.hex(' ')})")
    if base == RAX:
        raise HookShapeError(f"root window hook: the load reads through rax ({original.hex(' ')})")
    into_rax = bytes([rex, 0x8B, original[2] & 0xC7]) + original[3:]  # same field, reg field -> rax
    return PUSH_RAX + into_rax + b"\x48\xA3" + export + POP_RAX + original


def dance_body(original: bytes, export: bytes) -> bytes:
    """Dance game: `mov R, rax` + `cmp [rax+0x10], rsi` replaced. R keeps rax while
    the first 8 bytes of the move string go to the export."""
    if len(original) != 7 or original[:2] != b"\x48\x8B" or original[3:] != b"\x48\x39\x70\x10":
        raise HookShapeError(f"dance hook: unexpected bytes ({original.hex(' ')})")
    modrm = original[2]
    if modrm >> 6 != 3 or modrm & 7 != RAX:
        raise HookShapeError(f"dance hook: not a mov from rax ({original.hex(' ')})")
    keep = (modrm >> 3) & 7
    if keep in (RAX, RSP, RSI):
        raise HookShapeError(f"dance hook: rax is kept in a register it cannot use ({original.hex(' ')})")
    return (
        original[:3]  # mov R, rax
        + b"\x48\x8B\x00"  # mov rax, [rax]
        + b"\x48\xA3" + export  # mov [export], rax
        + bytes([0x48, 0x8B, 0xC0 | keep])  # mov rax, R
        + original[3:]  # cmp [rax+0x10], rsi
    )
