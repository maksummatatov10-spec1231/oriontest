#!/usr/bin/env python3
"""Patch Orion.swf: inject ExternalInterface cheat bridge into Game.update."""
from __future__ import annotations

import json
import os
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_SWF = ROOT / "Orion.swf"
DST_SWF = ROOT / "player" / "Orion.patched.swf"
DATA_DIR = ROOT / "player" / "data"


def u30(data: bytes, i: int):
    r = 0
    for sh in range(0, 35, 7):
        b = data[i]
        i += 1
        r |= (b & 0x7F) << sh
        if not (b & 0x80):
            break
    return r, i


def enc_u30(n: int) -> bytes:
    n &= 0xFFFFFFFF
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def enc_s24(n: int) -> bytes:
    n &= 0xFFFFFF
    return bytes((n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF))


def read_s32(data, i):
    r, i = u30(data, i)
    if r & 0x80000000:
        r -= 0x100000000
    return r, i


def parse_rect(data, pos):
    nbits = data[pos] >> 3
    return pos + (5 + nbits * 4 + 7) // 8


def load_swf(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw[:3] == b"CWS":
        return b"FWS" + raw[3:8] + zlib.decompress(raw[8:])
    if raw[:3] == b"FWS":
        return raw
    raise SystemExit(f"unsupported swf {raw[:3]}")


def write_swf(path: Path, data: bytes, compressed=True):
    ver = data[3]
    body = data[8:]
    if compressed:
        out = b"CWS" + bytes([ver]) + struct.pack("<I", 8 + len(body)) + zlib.compress(body, 9)
    else:
        out = b"FWS" + bytes([ver]) + struct.pack("<I", 8 + len(body)) + body
        out = out[:4] + struct.pack("<I", len(out)) + out[8:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    print(f"wrote {path} ({len(out)} bytes)")


def iter_tags(data: bytes):
    pos = parse_rect(data, 8) + 4
    while pos + 2 <= len(data):
        start = pos
        rec = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        code, length = rec >> 6, rec & 0x3F
        long_len = False
        if length == 0x3F:
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            long_len = True
        payload = data[pos : pos + length]
        yield start, code, long_len, payload
        pos += length
        if code == 0:
            break


def rebuild_swf(header: bytes, tags):
    """header is bytes up to (not including) first tag."""
    out = bytearray(header)
    for code, payload in tags:
        length = len(payload)
        if length >= 0x3F:
            out.extend(struct.pack("<H", (code << 6) | 0x3F))
            out.extend(struct.pack("<I", length))
        else:
            out.extend(struct.pack("<H", (code << 6) | length))
        out.extend(payload)
    # file length
    out[4:8] = struct.pack("<I", len(out))
    return bytes(out)


class Abc:
    def __init__(self, data: bytes):
        self.data = data
        self.ints = []
        self.uints = []
        self.doubles = []
        self.strings = []
        self.namespaces = []  # (kind, name_idx)
        self.nssets = []
        self.multinames = []  # (kind, ns, name, nset, extra)
        self.methods = []
        self.instances = []
        self.classes = []
        self.bodies = {}
        self.body_meta = {}
        self.string_count_off = 0
        self.strings_end = 0
        self.ns_count_off = 0
        self.ns_end = 0
        self.mn_count_off = 0
        self.mn_end = 0
        self.tail_off = 0  # offset after multinames
        self._parse()

    def str_at(self, idx):
        if idx == 0:
            return "*"
        if 0 < idx <= len(self.strings):
            return self.strings[idx - 1]
        return ""

    def ns_name(self, idx):
        if idx <= 0 or idx > len(self.namespaces):
            return ""
        kind, name_idx = self.namespaces[idx - 1]
        return self.str_at(name_idx)

    def mn_str(self, idx):
        if idx <= 0 or idx > len(self.multinames):
            return "*"
        kind, ns, name, nset, extra = self.multinames[idx - 1]
        n = self.str_at(name) if name else "*"
        nsn = self.ns_name(ns) if ns else ""
        return f"{nsn}::{n}" if nsn else n

    def _parse(self):
        abc = self.data
        i = 4
        def rlist(empty, reader):
            nonlocal i
            n, i = u30(abc, i)
            items = []
            if n == 0:
                return items
            start = 1 if empty else 0
            for _ in range(n - start if empty else n):
                v, i = reader(i)
                items.append(v)
            return items

        def r_s32(idx):
            return read_s32(abc, idx)

        def r_u32(idx):
            return u30(abc, idx)

        def r_d64(idx):
            return struct.unpack_from("<d", abc, idx)[0], idx + 8

        def r_str(idx):
            ln, ni = u30(abc, idx)
            s = abc[ni : ni + ln]
            ni += ln
            try:
                return s.decode("utf-8"), ni
            except Exception:
                return s.decode("latin1", "replace"), ni

        self.ints = rlist(True, r_s32)
        self.uints = rlist(True, r_u32)
        self.doubles = rlist(True, r_d64)
        self.string_count_off = i
        self.strings = rlist(True, r_str)
        self.strings_end = i

        def r_ns(idx):
            kind = abc[idx]
            idx += 1
            name_idx, idx = u30(abc, idx)
            return (kind, name_idx), idx

        self.ns_count_off = i
        self.namespaces = rlist(True, r_ns)
        self.ns_end = i

        def r_nss(idx):
            cnt, idx = u30(abc, idx)
            ids = []
            for _ in range(cnt):
                nid, idx = u30(abc, idx)
                ids.append(nid)
            return ids, idx

        self.nssets = rlist(True, r_nss)

        def r_mn(idx):
            kind = abc[idx]
            idx += 1
            ns = name = nset = extra = None
            if kind in (0x07, 0x0D):
                ns, idx = u30(abc, idx)
                name, idx = u30(abc, idx)
            elif kind in (0x0F, 0x10):
                name, idx = u30(abc, idx)
            elif kind in (0x11, 0x12):
                pass
            elif kind in (0x09, 0x0E):
                name, idx = u30(abc, idx)
                nset, idx = u30(abc, idx)
            elif kind in (0x1B, 0x1C):
                nset, idx = u30(abc, idx)
            elif kind == 0x1D:
                name, idx = u30(abc, idx)
                cnt, idx = u30(abc, idx)
                extra = []
                for _ in range(cnt):
                    p, idx = u30(abc, idx)
                    extra.append(p)
            else:
                raise ValueError(f"mn kind {kind:#x} at {idx}")
            return (kind, ns, name, nset, extra), idx

        self.mn_count_off = i
        self.multinames = rlist(True, r_mn)
        self.mn_end = i
        self.tail_off = i

        method_count, i = u30(abc, i)
        methods = []
        for _ in range(method_count):
            pcount, i = u30(abc, i)
            ret, i = u30(abc, i)
            params = []
            for _p in range(pcount):
                t, i = u30(abc, i)
                params.append(t)
            name_idx, i = u30(abc, i)
            flags = abc[i]
            i += 1
            if flags & 0x08:
                opt_count, i = u30(abc, i)
                for _o in range(opt_count):
                    _, i = u30(abc, i)
                    i += 1
            if flags & 0x80:
                for _p in range(pcount):
                    _, i = u30(abc, i)
            methods.append({"name_idx": name_idx, "flags": flags, "params": params, "ret": ret})
        self.methods = methods

        meta_count, i = u30(abc, i)
        for _ in range(meta_count):
            _, i = u30(abc, i)
            ic, i = u30(abc, i)
            for _k in range(ic):
                _, i = u30(abc, i)
                _, i = u30(abc, i)

        def read_traits(idx):
            tcount, idx = u30(abc, idx)
            traits = []
            for _ in range(tcount):
                name_idx, idx = u30(abc, idx)
                kind = abc[idx]
                idx += 1
                tkind = kind & 0x0F
                tattr = kind >> 4
                slot_id, idx = u30(abc, idx)
                info = {"name_idx": name_idx, "kind": tkind, "attr": tattr, "slot_id": slot_id, "mn": self.mn_str(name_idx)}
                if tkind in (0, 6):
                    type_idx, idx = u30(abc, idx)
                    vindex, idx = u30(abc, idx)
                    if vindex:
                        idx += 1
                    info["slot"] = "const" if tkind == 6 else "slot"
                elif tkind == 4:
                    classi, idx = u30(abc, idx)
                    info["class_i"] = classi
                    info["slot"] = "class"
                elif tkind == 5:
                    func, idx = u30(abc, idx)
                    info["method"] = func
                    info["slot"] = "function"
                elif tkind in (1, 2, 3):
                    method_i, idx = u30(abc, idx)
                    info["method"] = method_i
                    info["slot"] = {1: "method", 2: "getter", 3: "setter"}[tkind]
                else:
                    raise ValueError(tkind)
                if tattr & 0x4:
                    mcount, idx = u30(abc, idx)
                    for _m in range(mcount):
                        _, idx = u30(abc, idx)
                traits.append(info)
            return traits, idx

        class_count, i = u30(abc, i)
        instances = []
        for _ in range(class_count):
            name_idx, i = u30(abc, i)
            super_idx, i = u30(abc, i)
            flags = abc[i]
            i += 1
            if flags & 0x08:
                _, i = u30(abc, i)
            ifcount, i = u30(abc, i)
            for _f in range(ifcount):
                _, i = u30(abc, i)
            iinit, i = u30(abc, i)
            traits, i = read_traits(i)
            instances.append({"name": self.mn_str(name_idx), "iinit": iinit, "traits": traits})
        self.instances = instances

        classes = []
        for _ in range(class_count):
            cinit, i = u30(abc, i)
            traits, i = read_traits(i)
            classes.append({"cinit": cinit, "traits": traits})
        self.classes = classes

        script_count, i = u30(abc, i)
        for _ in range(script_count):
            _, i = u30(abc, i)
            _, i = read_traits(i)

        body_count, i = u30(abc, i)
        bodies = {}
        body_meta = {}
        for _ in range(body_count):
            method_i, i = u30(abc, i)
            max_stack_off = i
            max_stack, i = u30(abc, i)
            local_count_off = i
            local_count, i = u30(abc, i)
            init_scope, i = u30(abc, i)
            max_scope, i = u30(abc, i)
            code_len_off = i
            code_len, i = u30(abc, i)
            code_off = i
            code = abc[i : i + code_len]
            i += code_len
            ex_count, i = u30(abc, i)
            for _e in range(ex_count):
                for _x in range(5):
                    _, i = u30(abc, i)
            _, i = read_traits(i)
            bodies[method_i] = code
            body_meta[method_i] = {
                "max_stack": max_stack,
                "max_stack_off": max_stack_off,
                "local_count": local_count,
                "local_count_off": local_count_off,
                "code_len": code_len,
                "code_len_off": code_len_off,
                "code_off": code_off,
            }
        self.bodies = bodies
        self.body_meta = body_meta

    def find_qname(self, ns: str, name: str):
        for i, (kind, nsi, namei, nset, extra) in enumerate(self.multinames, start=1):
            if kind in (0x07, 0x0D) and self.str_at(namei) == name and self.ns_name(nsi) == ns:
                return i
        return 0

    def find_name_any(self, name: str):
        """First QName/Multiname with this name."""
        for i, (kind, nsi, namei, nset, extra) in enumerate(self.multinames, start=1):
            if namei and self.str_at(namei) == name:
                return i
        return 0

    def intern_string(self, s: str) -> int:
        if s in self.strings:
            return self.strings.index(s) + 1
        self.strings.append(s)
        return len(self.strings)

    def intern_ns(self, kind: int, name: str) -> int:
        ni = self.intern_string(name)
        for i, (k, n) in enumerate(self.namespaces, start=1):
            if k == kind and n == ni:
                return i
        self.namespaces.append((kind, ni))
        return len(self.namespaces)

    def intern_qname(self, ns: str, name: str, ns_kind=0x16) -> int:
        found = self.find_qname(ns, name)
        if found:
            return found
        nsi = self.intern_ns(ns_kind, ns)
        namei = self.intern_string(name)
        self.multinames.append((0x07, nsi, namei, None, None))
        return len(self.multinames)

    def encode_string_entry(self, s: str) -> bytes:
        b = s.encode("utf-8")
        return enc_u30(len(b)) + b

    def encode_ns_entry(self, kind, name_idx) -> bytes:
        return bytes([kind]) + enc_u30(name_idx)

    def encode_mn_entry(self, mn) -> bytes:
        kind, ns, name, nset, extra = mn
        out = bytes([kind])
        if kind in (0x07, 0x0D):
            out += enc_u30(ns) + enc_u30(name)
        elif kind in (0x0F, 0x10):
            out += enc_u30(name)
        elif kind in (0x11, 0x12):
            pass
        elif kind in (0x09, 0x0E):
            out += enc_u30(name) + enc_u30(nset)
        elif kind in (0x1B, 0x1C):
            out += enc_u30(nset)
        elif kind == 0x1D:
            out += enc_u30(name)
            extra = extra or []
            out += enc_u30(len(extra))
            for p in extra:
                out += enc_u30(p)
        else:
            raise ValueError(kind)
        return out

    def rebuild_prefix(self, orig_strings_n, orig_ns_n, orig_mn_n) -> bytes:
        """Rebuild from start through multinames using possibly extended pools.
        Only the appended entries are encoded; original pool bytes are reused
        for the original entries to avoid encoder mismatch.
        """
        abc = self.data
        # ints/uints/doubles unchanged: bytes [4 : string_count_off]
        head = bytearray(abc[: self.string_count_off])
        # strings
        orig_strings_bytes = abc[self.string_count_off : self.strings_end]
        # replace count
        # skip original count u30
        _, after_count = u30(abc, self.string_count_off)
        orig_payload = abc[after_count : self.strings_end]
        extra_s = b""
        for s in self.strings[orig_strings_n:]:
            extra_s += self.encode_string_entry(s)
        head.extend(enc_u30(len(self.strings) + 1 if self.strings else 0) if True else b"")
        # ABC string count is n including empty slot: 0 means empty, else n-1 strings follow
        str_count = 0 if not self.strings else len(self.strings) + 1
        # rebuild strings section properly
        head = bytearray(abc[: self.string_count_off])
        head.extend(enc_u30(str_count))
        head.extend(orig_payload)
        head.extend(extra_s)

        # namespaces
        _, ns_after_count = u30(abc, self.ns_count_off)
        orig_ns_payload = abc[ns_after_count : self.ns_end]
        extra_ns = b""
        for kind, name_idx in self.namespaces[orig_ns_n:]:
            extra_ns += self.encode_ns_entry(kind, name_idx)
        ns_count = 0 if not self.namespaces else len(self.namespaces) + 1
        head.extend(enc_u30(ns_count))
        head.extend(orig_ns_payload)
        head.extend(extra_ns)

        # nssets unchanged
        head.extend(abc[self.ns_end : self.mn_count_off])

        # multinames
        _, mn_after_count = u30(abc, self.mn_count_off)
        orig_mn_payload = abc[mn_after_count : self.mn_end]
        extra_mn = b""
        for mn in self.multinames[orig_mn_n:]:
            extra_mn += self.encode_mn_entry(mn)
        mn_count = 0 if not self.multinames else len(self.multinames) + 1
        head.extend(enc_u30(mn_count))
        head.extend(orig_mn_payload)
        head.extend(extra_mn)
        return bytes(head)


class Asm:
    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, *b):
        self.code.extend(b)

    def u30(self, n):
        self.code.extend(enc_u30(n))

    def op(self, opc):
        self.code.append(opc)

    def label(self, name):
        self.labels[name] = len(self.code)

    def jump(self, opc, lab):
        self.op(opc)
        self.fixups.append((len(self.code), lab))
        self.code.extend(b"\x00\x00\x00")

    def getlocal0(self):
        self.op(0xD0)

    def getlocal1(self):
        self.op(0xD1)

    def getlocal(self, n):
        if n == 0:
            self.op(0xD0)
        elif n == 1:
            self.op(0xD1)
        elif n == 2:
            self.op(0xD2)
        elif n == 3:
            self.op(0xD3)
        else:
            self.op(0x62)
            self.u30(n)

    def setlocal(self, n):
        if n == 1:
            self.op(0xD4)
        elif n == 2:
            self.op(0xD5)
        elif n == 3:
            self.op(0xD6)
        else:
            self.op(0x63)
            self.u30(n)

    def pushbyte(self, n):
        self.op(0x24)
        self.code.append(n & 0xFF)

    def pushshort(self, n):
        self.op(0x25)
        self.u30(n & 0xFFFFFFFF)

    def pushint_idx(self, idx):
        self.op(0x2D)
        self.u30(idx)

    def pushstring(self, idx):
        self.op(0x2C)
        self.u30(idx)

    def pushnull(self):
        self.op(0x20)

    def pushtrue(self):
        self.op(0x26)

    def pushfalse(self):
        self.op(0x27)

    def pop(self):
        self.op(0x29)

    def dup(self):
        self.op(0x2A)

    def getlex(self, mn):
        self.op(0x60)
        self.u30(mn)

    def getproperty(self, mn):
        self.op(0x66)
        self.u30(mn)

    def setproperty(self, mn):
        self.op(0x61)
        self.u30(mn)

    def callproperty(self, mn, argc):
        self.op(0x46)
        self.u30(mn)
        self.u30(argc)

    def callpropvoid(self, mn, argc):
        self.op(0x4F)
        self.u30(mn)
        self.u30(argc)

    def findpropstrict(self, mn):
        self.op(0x5D)
        self.u30(mn)

    def constructprop(self, mn, argc):
        self.op(0x4A)
        self.u30(mn)
        self.u30(argc)

    def convert_i(self):
        self.op(0x73)

    def convert_d(self):
        self.op(0x75)

    def convert_b(self):
        self.op(0x76)

    def coerce_a(self):
        self.op(0x82)

    def add(self):
        self.op(0xA0)

    def divide(self):
        self.op(0xA3)

    def equals(self):
        self.op(0xAB)

    def not_(self):
        self.op(0x96)

    def iffalse(self, lab):
        self.jump(0x12, lab)

    def iftrue(self, lab):
        self.jump(0x11, lab)

    def ifeq(self, lab):
        self.jump(0x13, lab)

    def ifne(self, lab):
        self.jump(0x14, lab)

    def jump_to(self, lab):
        self.jump(0x10, lab)

    def returnvoid(self):
        self.op(0x47)

    def kill(self, n):
        self.op(0x08)
        self.u30(n)

    def finish(self) -> bytes:
        code = bytearray(self.code)
        for pos, lab in self.fixups:
            if lab not in self.labels:
                raise KeyError(lab)
            rel = self.labels[lab] - (pos + 3)
            code[pos : pos + 3] = enc_s24(rel)
        return bytes(code)


def find_game_update(abc: Abc):
    for inst in abc.instances:
        if inst["name"] == "orion::Game":
            for t in inst["traits"]:
                if t.get("slot") == "method" and t["mn"].endswith("update") and t["mn"].count("::") <= 1:
                    # method update — skip preUpdate/postUpdate
                    simple = t["mn"].split("::")[-1]
                    if simple == "update":
                        return t["method"]
            # fallback
            for t in inst["traits"]:
                if t.get("slot") == "method" and t["mn"].endswith("::update"):
                    return t["method"]
    raise SystemExit("orion::Game.update not found")


def build_bridge(abc: Abc, orig_code: bytes) -> bytes:
    # Intern new names
    ei = abc.intern_qname("flash.external", "ExternalInterface")
    call_mn = abc.find_name_any("call") or abc.intern_qname("", "call")
    avail_mn = abc.find_name_any("available") or abc.intern_qname("", "available")
    player_mn = abc.find_name_any("player")
    world_mn = abc.find_name_any("world")
    inventory_mn = abc.find_name_any("inventory")
    health_mn = abc.find_name_any("health")
    max_health_mn = abc.find_qname("orion.worlds.entities", "maxHealth") or abc.find_name_any("maxHealth")
    immortal_mn = abc.find_name_any("immortal")
    level_mn = abc.find_name_any("level")
    speed_mn = abc.find_name_any("moveSpeed")
    stage_mn = abc.find_name_any("stage")
    fr_mn = abc.find_name_any("frameRate") or abc.intern_qname("", "frameRate")
    items_mn = abc.find_name_any("ITEMS")
    item_cls = abc.find_qname("orion.worlds", "Item")
    stack_cls = abc.find_qname("orion.worlds", "ItemStack")
    add_mn = None
    # prefer CharacterInventory.add / InventoryBase.add — any 'add' on inventory works via callpropvoid
    add_mn = abc.find_qname("orion.inventories", "add") or abc.find_name_any("add")
    count_mn = abc.find_name_any("count")
    set_time = abc.find_name_any("setGameTime")
    add_creature = abc.find_name_any("addCreature")
    make_unique = abc.find_name_any("makeUnique")
    pos_mn = abc.find_name_any("position")
    x_mn = abc.find_name_any("x")
    y_mn = abc.find_name_any("y")
    star_mn = 0
    for i, (kind, nsi, namei, nset, extra) in enumerate(abc.multinames, start=1):
        if kind in (0x1B, 0x1C):  # MultinameL runtime index
            star_mn = i
            break
    if not star_mn:
        for i, (kind, nsi, namei, nset, extra) in enumerate(abc.multinames, start=1):
            if kind in (0x09, 0x0E) and abc.str_at(namei) in ("*", ""):
                star_mn = i
                break

    s_op = abc.intern_string("orionDevOp")
    s_a = abc.intern_string("orionDevA")
    s_b = abc.intern_string("orionDevB")

    bosses = [
        "orion.worlds.entities.mobs.unique::UGargoyleEntity",
        "orion.worlds.entities.mobs.unique::UGnomeEntity",
        "orion.worlds.entities.mobs.unique::UBigSpiderEntity",
        "orion.worlds.entities.mobs.unique::UTransformerEntity",
        "orion.worlds.entities.mobs.unique::UfoEntity",
        "orion.worlds.entities.mobs.unique::UZombieEntity",
        "orion.worlds.entities.mobs.unique::UfoShadowEntity",
        "orion.worlds.entities.mobs.unique::UShadowZombieEntity",
        "orion.worlds.entities.mobs.unique::UBigShadowSpiderEntity",
        "orion.worlds.entities.mobs.unique::UStoneGolemEntity",
        "orion.worlds.entities.mobs::DemonEntity",
        "orion.worlds.entities.mobs::MechanicalGolemEvilEntity",
        "orion.worlds.entities.mobs::FireRobotEntity",
        "orion.worlds.entities.mobs::EvilBigSpiderEntity",
        "orion.worlds.entities.mobs::StoneGolemEvilEntity",
    ]
    boss_mns = []
    for b in bosses:
        ns, _, name = b.rpartition("::")
        mn = abc.find_qname(ns, name)
        boss_mns.append(mn)
        print(f"  boss {name}: mn={mn}")

    needed = {
        "ei": ei,
        "call": call_mn,
        "available": avail_mn,
        "player": player_mn,
        "world": world_mn,
        "inventory": inventory_mn,
        "health": health_mn,
        "maxHealth": max_health_mn,
        "immortal": immortal_mn,
        "level": level_mn,
        "moveSpeed": speed_mn,
        "stage": stage_mn,
        "frameRate": fr_mn,
        "ITEMS": items_mn,
        "Item": item_cls,
        "ItemStack": stack_cls,
        "add": add_mn,
        "count": count_mn,
        "setGameTime": set_time,
        "addCreature": add_creature,
        "makeUnique": make_unique,
        "position": pos_mn,
        "x": x_mn,
        "y": y_mn,
        "index": star_mn,
    }
    print("multinames:", needed)
    missing = [k for k, v in needed.items() if not v]
    if missing:
        raise SystemExit(f"missing multinames: {missing}")

    # locals: 0 this, 1 elapsed (orig), 2 orig, 3 orig, 4 player, 5 op, 6 argA, 7 argB, 8 tmp
    L_PLAYER, L_OP, L_A, L_B, L_TMP = 4, 5, 6, 7, 8

    a = Asm()
    a.getlocal0()
    a.op(0x30)  # pushscope — original also does this; we'll skip original's first 2 bytes
    # if player == null -> skip cheats but still try EI? skip all extra
    a.getlocal0()
    a.getproperty(player_mn)
    a.pushnull()
    a.ifne("has_player")
    a.jump_to("do_orig")
    a.label("has_player")
    a.getlocal0()
    a.getproperty(player_mn)
    a.setlocal(L_PLAYER)

    # god: if immortal, health = maxHealth
    a.getlocal(L_PLAYER)
    a.getproperty(immortal_mn)
    a.convert_b()
    a.iffalse("no_god")
    a.getlocal(L_PLAYER)
    a.getlocal(L_PLAYER)
    a.getproperty(max_health_mn)
    a.setproperty(health_mn)
    a.label("no_god")

    # ExternalInterface.available?
    a.getlex(ei)
    a.getproperty(avail_mn)
    a.convert_b()
    a.iffalse("do_orig")

    def ei_call(str_idx, dest_local):
        a.getlex(ei)
        a.pushstring(str_idx)
        a.callproperty(call_mn, 1)
        a.convert_i()
        a.setlocal(dest_local)

    ei_call(s_op, L_OP)
    a.getlocal(L_OP)
    a.pushbyte(0)
    a.ifeq("do_orig")

    ei_call(s_a, L_A)
    ei_call(s_b, L_B)

    # op==1 level
    a.getlocal(L_OP)
    a.pushbyte(1)
    a.ifne("op2")
    a.getlocal(L_PLAYER)
    a.getlocal(L_A)
    a.setproperty(level_mn)
    a.jump_to("do_orig")

    # op==2 health
    a.label("op2")
    a.getlocal(L_OP)
    a.pushbyte(2)
    a.ifne("op3")
    a.getlocal(L_PLAYER)
    a.getlocal(L_A)
    a.setproperty(health_mn)
    a.jump_to("do_orig")

    # op==3 god on
    a.label("op3")
    a.getlocal(L_OP)
    a.pushbyte(3)
    a.ifne("op4")
    a.getlocal(L_PLAYER)
    a.pushtrue()
    a.setproperty(immortal_mn)
    a.jump_to("do_orig")

    # op==4 god off
    a.label("op4")
    a.getlocal(L_OP)
    a.pushbyte(4)
    a.ifne("op5")
    a.getlocal(L_PLAYER)
    a.pushfalse()
    a.setproperty(immortal_mn)
    a.jump_to("do_orig")

    # op==5 give item  A=id B=count
    a.label("op5")
    a.getlocal(L_OP)
    a.pushbyte(5)
    a.ifne("op6")
    a.getlex(item_cls)
    a.getproperty(items_mn)
    a.getlocal(L_A)
    a.getproperty(star_mn)
    a.coerce_a()
    a.setlocal(L_TMP)
    a.getlocal(L_TMP)
    a.pushnull()
    a.ifeq("do_orig")
    a.getlocal0()
    a.getproperty(inventory_mn)
    a.findpropstrict(stack_cls)
    a.getlocal(L_TMP)
    a.constructprop(stack_cls, 1)
    a.dup()
    a.getlocal(L_B)
    a.setproperty(count_mn)
    a.callpropvoid(add_mn, 1)
    a.jump_to("do_orig")

    # op==6 spawn boss A=index
    a.label("op6")
    a.getlocal(L_OP)
    a.pushbyte(6)
    a.ifne("op7")
    # compute x,y
    # tmp will hold mob
    for i, mn in enumerate(boss_mns):
        lab_next = f"b{i+1}"
        lab_spawn = f"bs{i}"
        a.getlocal(L_A)
        a.pushbyte(i)
        a.ifeq(lab_spawn)
        a.jump_to(lab_next)
        a.label(lab_spawn)
        if mn:
            a.findpropstrict(mn)
            a.getlocal(L_PLAYER)
            a.getproperty(pos_mn)
            a.getproperty(x_mn)
            a.pushbyte(80)
            a.add()
            a.getlocal(L_PLAYER)
            a.getproperty(pos_mn)
            a.getproperty(y_mn)
            a.constructprop(mn, 2)
            a.setlocal(L_TMP)
            # makeUnique is a protected multiname — skip (boss classes already unique)
            a.getlocal0()
            a.getproperty(world_mn)
            a.getlocal(L_TMP)
            a.callpropvoid(add_creature, 1)
        a.jump_to("do_orig")
        a.label(lab_next)
    a.jump_to("do_orig")

    # op==7 speed  A = tenths
    a.label("op7")
    a.getlocal(L_OP)
    a.pushbyte(7)
    a.ifne("op8")
    a.getlocal(L_PLAYER)
    a.getlocal(L_A)
    a.convert_d()
    a.pushbyte(10)
    a.convert_d()
    a.divide()
    a.setproperty(speed_mn)
    a.jump_to("do_orig")

    # op==8 time  A=hours B=minutes
    a.label("op8")
    a.getlocal(L_OP)
    a.pushbyte(8)
    a.ifne("op9")
    a.getlocal0()
    a.getproperty(world_mn)
    a.getlocal(L_A)
    a.getlocal(L_B)
    a.callpropvoid(set_time, 2)
    a.jump_to("do_orig")

    # op==9 fps
    a.label("op9")
    a.getlocal(L_OP)
    a.pushbyte(9)
    a.ifne("do_orig")
    a.getlocal0()
    a.getproperty(stage_mn)
    a.getlocal(L_A)
    a.convert_d()
    a.setproperty(fr_mn)

    a.label("do_orig")
    # original method starts with getlocal0 + pushscope; skip those 2 bytes
    orig = orig_code
    if orig[:2] == bytes((0xD0, 0x30)):
        orig = orig[2:]
    prefix = a.finish()
    return prefix + orig


def extract_items(abc: Abc) -> list:
    """Walk Item.staticInit for constructor ids + setName."""
    inst = next(x for x in abc.instances if x["name"] == "orion.worlds::Item")
    mid = None
    cls = abc.classes[abc.instances.index(inst)]
    for t in cls["traits"]:
        if t["mn"].endswith("staticInit"):
            mid = t["method"]
            break
    if mid is None:
        return []
    code = abc.bodies[mid]
    # light disasm to track last int and setName strings
    items = []
    i = 0
    last_int = None
    last_constructed_id = None
    pending = {}

    def ru30(pos):
        return u30(code, pos)

    while i < len(code):
        op = code[i]
        i += 1
        if op == 0x24:  # pushbyte
            v = code[i]
            if v >= 128:
                v -= 256
            i += 1
            last_int = v
        elif op == 0x25:  # pushshort
            last_int, i = ru30(i)
        elif op == 0x2D:  # pushint
            idx, i = ru30(i)
            last_int = abc.ints[idx - 1] if 0 < idx <= len(abc.ints) else None
        elif op == 0x2C:  # pushstring
            idx, i = ru30(i)
            last_str = abc.str_at(idx)
            # look ahead? we'll catch on callprop setName
            pending["str"] = last_str
        elif op in (0x4A,):  # constructprop
            mn, i = ru30(i)
            argc, i = ru30(i)
            if argc >= 1 and last_int is not None and last_int >= 0:
                last_constructed_id = last_int
                pending["id"] = last_int
                pending["cls"] = abc.mn_str(mn)
        elif op in (0x46, 0x4F):  # callproperty / callpropvoid
            mn, i = ru30(i)
            argc, i = ru30(i)
            name = abc.mn_str(mn).split("::")[-1]
            if name == "setName" and pending.get("str") is not None:
                items.append(
                    {
                        "id": pending.get("id", last_constructed_id),
                        "key": pending["str"],
                        "cls": pending.get("cls", ""),
                    }
                )
        elif op in (0x60, 0x5D, 0x5E, 0x61, 0x66, 0x68, 0x80, 0x86, 0xB2):
            _, i = ru30(i)
        elif op in (0x41, 0x42, 0x49, 0x53, 0x55, 0x56):
            _, i = ru30(i)
        elif op == 0x2F:
            _, i = ru30(i)
        elif op == 0x2E:
            _, i = ru30(i)
        elif op in (0x62, 0x63, 0x08, 0x40, 0x58, 0x6C, 0x6D):
            _, i = ru30(i)
        elif op in (0x43, 0x44, 0x45):
            _, i = ru30(i)
            _, i = ru30(i)
        elif op in (0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x0C, 0x0D, 0x0E, 0x0F):
            i += 3
        elif op == 0x65:
            i += 1
        elif op == 0x32:
            _, i = ru30(i)
            _, i = ru30(i)
        elif op == 0x1B:
            i += 3
            n, i = ru30(i)
            i += 3 * (n + 1)
        else:
            pass
    # unique by id
    by_id = {}
    for it in items:
        if it["id"] is None:
            continue
        by_id[it["id"]] = it
    return [by_id[k] for k in sorted(by_id)]


def load_loc_from_swf(data: bytes) -> dict:
    """Find the Russian JSON blob among DefineBinaryData tags."""
    ru = None
    en = None
    for start, code, long_len, payload in iter_tags(data):
        if code != 87 or len(payload) < 6:
            continue
        blob = payload[6:]
        if not blob.startswith(b"{"):
            continue
        try:
            obj = json.loads(blob.decode("utf-8"))
        except Exception:
            continue
        desc = obj.get("ORION GAME DESCRIPTION", "")
        if "Улучшенная версия" in desc or "выживать" in desc:
            ru = obj
        elif "enhanced version of the great game" in desc:
            en = obj
    return ru or {}, en or {}


BOSSES = [
    {"id": 0, "class": "UGargoyleEntity", "name": "Древний Страж", "key": "boss_gargoule"},
    {"id": 1, "class": "UGnomeEntity", "name": "Свергнутый Король", "key": "boss_gnome"},
    {"id": 2, "class": "UBigSpiderEntity", "name": "Король Москитон", "key": "boss_spider"},
    {"id": 3, "class": "UTransformerEntity", "name": "Древний Страж (машина)", "key": "boss_transformer"},
    {"id": 4, "class": "UfoEntity", "name": "Император Финалиум", "key": "boss_ufo"},
    {"id": 5, "class": "UZombieEntity", "name": "Тиран", "key": "boss_zombie"},
    {"id": 6, "class": "UfoShadowEntity", "name": "Призрак Императора", "key": "boss_shadow_ufo"},
    {"id": 7, "class": "UShadowZombieEntity", "name": "Призрак Тирана", "key": "boss_shadow_tyrant"},
    {"id": 8, "class": "UBigShadowSpiderEntity", "name": "Призрак Москитона", "key": "boss_shadow_mosquiton"},
    {"id": 9, "class": "UStoneGolemEntity", "name": "Каменный голем", "key": "mob_stone_golem"},
    {"id": 10, "class": "DemonEntity", "name": "Пламенный Демон", "key": "mob_demon"},
    {"id": 11, "class": "MechanicalGolemEvilEntity", "name": "Злой механический голем", "key": "mob_mech_golem"},
    {"id": 12, "class": "FireRobotEntity", "name": "Пламенный Робот", "key": "mob_fire_robot"},
    {"id": 13, "class": "EvilBigSpiderEntity", "name": "Злой большой жук", "key": "mob_evil_big_spider"},
    {"id": 14, "class": "StoneGolemEvilEntity", "name": "Злой каменный голем", "key": "mob_stone_golem"},
]


def main():
    print("loading", SRC_SWF)
    data = load_swf(SRC_SWF)
    header_end = parse_rect(data, 8) + 4
    header = data[:header_end]
    tags = []
    abc_index = None
    abc_name = None
    abc_flags = 0
    abc_bytes = None
    for start, code, long_len, payload in iter_tags(data):
        if code == 82:
            flags = struct.unpack_from("<I", payload, 0)[0]
            z = payload.find(b"\x00", 4)
            name = payload[4:z].decode("utf-8", "replace")
            abc = payload[z + 1 :]
            print(f"DoABC2 {name!r} flags={flags} len={len(abc)}")
            if name == "frame2" or (abc_bytes is None or len(abc) > len(abc_bytes or b"")):
                abc_index = len(tags)
                abc_name = name
                abc_flags = flags
                abc_bytes = abc
        tags.append((code, payload))

    if abc_bytes is None:
        raise SystemExit("no ABC")

    abc = Abc(abc_bytes)
    orig_s, orig_ns, orig_mn = len(abc.strings), len(abc.namespaces), len(abc.multinames)
    print(f"strings={orig_s} ns={orig_ns} mn={orig_mn} instances={len(abc.instances)}")

    mid = find_game_update(abc)
    meta = abc.body_meta[mid]
    orig_code = abc.bodies[mid]
    print(f"Game.update method={mid} code_len={len(orig_code)} stack={meta['max_stack']} locals={meta['local_count']}")

    new_code = build_bridge(abc, orig_code)
    print(f"new update code_len={len(new_code)} (delta {len(new_code)-len(orig_code)})")
    print(f"pool delta strings={len(abc.strings)-orig_s} ns={len(abc.namespaces)-orig_ns} mn={len(abc.multinames)-orig_mn}")

    prefix = abc.rebuild_prefix(orig_s, orig_ns, orig_mn)
    tail = abc_bytes[abc.tail_off :]
    # offsets in tail: body fields are relative to original abc
    shift = len(prefix) - abc.tail_off
    # rebuild whole abc = prefix + tail, then patch body inside
    new_abc = bytearray(prefix + tail)

    def map_off(orig_off):
        if orig_off >= abc.tail_off:
            return orig_off + shift
        raise ValueError("offset in prefix")

    # patch max_stack, local_count, code_len, code
    def patch_u30_field(orig_off, new_val, old_val):
        off = map_off(orig_off)
        old_enc = enc_u30(old_val)
        # original encoding at off
        _, after = u30(bytes(new_abc), off)
        old_len = after - off
        new_enc = enc_u30(new_val)
        new_abc[off : off + old_len] = new_enc
        return len(new_enc) - old_len

    # If u30 encodings of max_stack/local_count/code_len change size, subsequent offsets move.
    # Use same-width encoding when possible by padding? u30 can't pad easily.
    # We'll apply from the end of the body header backwards... actually code is last of these.
    d1 = patch_u30_field(meta["max_stack_off"], max(meta["max_stack"], 10), meta["max_stack"])
    if d1:
        meta["local_count_off"] += d1
        meta["code_len_off"] += d1
        meta["code_off"] += d1
    d2 = patch_u30_field(meta["local_count_off"], max(meta["local_count"], 10), meta["local_count"])
    if d2:
        meta["code_len_off"] += d2
        meta["code_off"] += d2
    d3 = patch_u30_field(meta["code_len_off"], len(new_code), meta["code_len"])
    if d3:
        meta["code_off"] += d3
    code_off = map_off(meta["code_off"]) if meta["code_off"] >= abc.tail_off else None
    # After previous patches, map_off uses original offsets. code_off in meta was updated by d1+d2+d3
    # relative to original abc. Recompute:
    code_off = meta["code_off"] + shift
    # Wait: meta['code_off'] was original, we added d1+d2+d3 which already accounts for u30 size
    # changes in the NEW abc. shift is prefix growth. So new code_off = orig_code_off + shift + d1+d2+d3
    # I already added d1+d2+d3 into meta['code_off']. Then + shift. Good.
    new_abc[code_off : code_off + meta["code_len"]] = new_code

    new_payload = struct.pack("<I", abc_flags) + abc_name.encode("utf-8") + b"\x00" + bytes(new_abc)
    tags[abc_index] = (82, new_payload)

    out = rebuild_swf(header, tags)
    write_swf(DST_SWF, out, compressed=True)

    # catalogs
    ru, en = load_loc_from_swf(data)
    raw_items = extract_items(abc)
    catalog = []
    for it in raw_items:
        key = it["key"]
        ru_name = ru.get("item_" + key) or ru.get("block_" + key) or ru.get(key) or en.get("item_" + key) or key
        catalog.append({"id": it["id"], "key": key, "name": ru_name, "cls": it["cls"].split("::")[-1]})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "items.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    bosses = []
    for b in BOSSES:
        bosses.append({**b, "name": ru.get(b["key"], b["name"])})
    (DATA_DIR / "bosses.json").write_text(json.dumps(bosses, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"items={len(catalog)} bosses={len(bosses)}")
    print("done")


if __name__ == "__main__":
    main()
