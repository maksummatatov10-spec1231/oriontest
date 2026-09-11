#!/usr/bin/env python3
"""Disassemble Game.update with stack depths."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from patch_orion import Abc, find_game_update, iter_tags, load_swf, u30


def get_frame2(path: Path) -> Abc:
    data = load_swf(path)
    for start, code, long_len, payload in iter_tags(data):
        if code == 82:
            z = payload.find(b"\x00", 4)
            if payload[4:z] == b"frame2":
                return Abc(payload[z + 1 :])
    raise SystemExit("no frame2")


def mn_rt(abc: Abc, idx: int) -> int:
    if idx <= 0 or idx > len(abc.multinames):
        return 0
    k = abc.multinames[idx - 1][0]
    if k in (0x11, 0x12, 0x1B, 0x1C):
        return 1
    if k in (0x13, 0x14):
        return 2
    return 0


def disasm(abc: Abc, code: bytes):
    ins = []
    i = 0

    def ru():
        nonlocal i
        v, i = u30(code, i)
        return v

    while i < len(code):
        off = i
        op = code[i]
        i += 1
        name = f"{op:02x}"
        d = 0
        fall = True
        tgts: list[int] = []
        if op == 0x10:
            n = int.from_bytes(code[i : i + 3], "little")
            if n >= 0x800000:
                n -= 0x1000000
            i += 3
            tgts = [i + n]
            fall = False
            name = "jump"
            d = 0
        elif op in (0x11, 0x12):
            n = int.from_bytes(code[i : i + 3], "little")
            if n >= 0x800000:
                n -= 0x1000000
            i += 3
            tgts = [i + n]
            name = "iftrue" if op == 0x11 else "iffalse"
            d = -1
        elif op in (0x0C, 0x0D, 0x0E, 0x0F, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A):
            n = int.from_bytes(code[i : i + 3], "little")
            if n >= 0x800000:
                n -= 0x1000000
            i += 3
            tgts = [i + n]
            d = -2
            name = {
                0x0C: "ifnlt",
                0x0D: "ifnle",
                0x0E: "ifngt",
                0x0F: "ifnge",
                0x13: "ifeq",
                0x14: "ifne",
                0x15: "iflt",
                0x16: "ifle",
                0x17: "ifgt",
                0x18: "ifge",
            }.get(op, "if")
        elif op == 0x24:
            v = code[i]
            i += 1
            name = f"pushbyte {v}"
            d = 1
        elif op == 0x25:
            v = ru()
            name = f"pushshort {v}"
            d = 1
        elif op == 0x2C:
            v = ru()
            name = f"pushstring {abc.str_at(v)!r}"[:70]
            d = 1
        elif op == 0x2D:
            v = ru()
            name = f"pushint #{v}"
            d = 1
        elif op == 0x20:
            name = "pushnull"
            d = 1
        elif op == 0x21:
            name = "pushundefined"
            d = 1
        elif op == 0x26:
            name = "pushtrue"
            d = 1
        elif op == 0x27:
            name = "pushfalse"
            d = 1
        elif op == 0x29:
            name = "pop"
            d = -1
        elif op == 0x2A:
            name = "dup"
            d = 1
        elif op == 0x2B:
            name = "swap"
            d = 0
        elif op == 0x30:
            name = "pushscope"
            d = -1
        elif op == 0x46:
            mn = ru()
            argc = ru()
            extra = mn_rt(abc, mn)
            name = f"callproperty {abc.mn_str(mn)} argc={argc}"
            d = -argc - extra
        elif op == 0x4F:
            mn = ru()
            argc = ru()
            extra = mn_rt(abc, mn)
            name = f"callpropvoid {abc.mn_str(mn)} argc={argc}"
            d = -(argc + 1) - extra
        elif op == 0x4A:
            mn = ru()
            argc = ru()
            extra = mn_rt(abc, mn)
            name = f"constructprop {abc.mn_str(mn)} argc={argc}"
            d = -argc - extra
        elif op == 0x5D:
            mn = ru()
            extra = mn_rt(abc, mn)
            name = f"findpropstrict {abc.mn_str(mn)}"
            d = 1 - extra
        elif op == 0x60:
            mn = ru()
            extra = mn_rt(abc, mn)
            name = f"getlex {abc.mn_str(mn)}"
            d = 1 - extra
        elif op == 0x66:
            mn = ru()
            extra = mn_rt(abc, mn)
            name = f"getproperty {abc.mn_str(mn)} rt={extra}"
            d = -extra
        elif op == 0x61:
            mn = ru()
            extra = mn_rt(abc, mn)
            name = f"setproperty {abc.mn_str(mn)}"
            d = -2 - extra
        elif op == 0x68:
            mn = ru()
            extra = mn_rt(abc, mn)
            name = f"initproperty {abc.mn_str(mn)}"
            d = -2 - extra
        elif op == 0x62:
            v = ru()
            name = f"getlocal {v}"
            d = 1
        elif op == 0x63:
            v = ru()
            name = f"setlocal {v}"
            d = -1
        elif op == 0xD0:
            name = "getlocal0"
            d = 1
        elif op == 0xD1:
            name = "getlocal1"
            d = 1
        elif op == 0xD2:
            name = "getlocal2"
            d = 1
        elif op == 0xD3:
            name = "getlocal3"
            d = 1
        elif op == 0xD4:
            name = "setlocal1"
            d = -1
        elif op == 0xD5:
            name = "setlocal2"
            d = -1
        elif op == 0xD6:
            name = "setlocal3"
            d = -1
        elif op == 0x47:
            name = "returnvoid"
            fall = False
            d = 0
        elif op == 0x48:
            name = "returnvalue"
            fall = False
            d = -1
        elif op in (0x70, 0x73, 0x74, 0x75, 0x76, 0x77, 0x82, 0x85, 0x90, 0x91, 0x93, 0x95, 0x96, 0x97):
            name = {
                0x73: "convert_i",
                0x75: "convert_d",
                0x76: "convert_b",
                0x82: "coerce_a",
                0x96: "not",
                0x70: "convert_s",
            }.get(op, "conv")
            d = 0
        elif op in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB0):
            name = {0xA0: "add", 0xA1: "sub", 0xA2: "mul", 0xA3: "div", 0xA5: "lshift", 0xAB: "equals"}.get(op, "binop")
            d = -1
        elif op == 0x80:
            mn = ru()
            name = f"coerce {abc.mn_str(mn)}"
            d = 0
        elif op == 0x64:
            name = "getglobalscope"
            d = 1
        elif op == 0x65:
            v = code[i]
            i += 1
            name = f"getscopeobject {v}"
            d = 1
        elif op == 0x1D:
            name = "popscope"
            d = 0
        elif op == 0x09:
            name = "labelop"
            d = 0
        elif op == 0x02:
            name = "nop"
            d = 0
        else:
            name = f"UNKNOWN {op:#x}"
            ins.append((off, name, d, tgts, fall, i))
            break
        ins.append((off, name, d, tgts, fall, i))
    return ins


def verify(ins, code_len):
    offs = {ins[k][0]: k for k in range(len(ins))}
    stack = {0: 0}
    work = [0]
    err = []
    maxs = 0
    seen = set()
    while work:
        pc = work.pop()
        if pc in seen:
            continue
        seen.add(pc)
        if pc not in offs:
            err.append(f"bad pc {pc}")
            continue
        k = offs[pc]
        st = stack[pc]
        off, name, d, tgts, fall, nxt = ins[k]
        nst = st + d
        if nst < 0:
            err.append(f"{off} UNDERFLOW {name} st={st} d={d}")
            continue
        maxs = max(maxs, nst)
        dests = list(tgts)
        if fall:
            dests.append(nxt)
        for dst in dests:
            if dst == code_len:
                continue
            if dst in stack and stack[dst] != nst:
                err.append(f"MERGE {dst} stack {stack[dst]} vs {nst} from {off} {name}")
            if dst not in stack:
                stack[dst] = nst
            work.append(dst)
    return stack, maxs, err, seen


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/user/oriontest/orion_menu_patch1.swf")
    abc = get_frame2(path)
    mid = find_game_update(abc)
    code = abc.bodies[mid]
    orig_at = code.find(bytes.fromhex("27d6d066b207"))
    print(f"{path.name} update len={len(code)} orig_tail@{orig_at}")
    ins = disasm(abc, code)
    print("decoded", len(ins), "last", ins[-1][0], ins[-1][1])
    stack, maxs, err, seen = verify(ins, len(code))
    print("visited", len(seen), "maxstack", maxs, "errors", len(err))
    for e in err[:40]:
        print(" ", e)
    print("--- first 80 ---")
    for t in ins[:80]:
        print(f"{t[0]:5d} st={stack.get(t[0], '?')} {t[1]}")
    if orig_at > 0:
        print("--- last 15 of prefix ---")
        pref = [t for t in ins if t[0] < orig_at]
        for t in pref[-15:]:
            print(f"{t[0]:5d} st={stack.get(t[0], '?')} {t[1]}")


if __name__ == "__main__":
    main()
