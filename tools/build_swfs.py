#!/usr/bin/env python3
"""Build orion_menu_patchN.swf — one in-SWF menu (cheats + graphics + settings).

GameProcess.fixedUpdate calls game.preUpdate/update/postUpdate.
SurvivalGame overrides preUpdate — Game.preUpdate is never run in-game.
Patch SurvivalGame.preUpdate (keep original tail) and leave Game.update intact.

AVM2 Overview + ASC typecheck dump (Error #1030):
- pushshort is a 16-bit signed operand; 24-bit RGB must be built with lshift.
- extra locals are initialized with pushundefined + coerce_a, not pushnull.
- new QNames are avoided; dynamic props use existing MultinameL + strings.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from patch_orion import (
    Abc,
    Asm,
    enc_s24,
    enc_u30,
    find_game_update,
    iter_tags,
    load_swf,
    parse_rect,
    rebuild_swf,
    u30,
    write_swf,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Orion.swf"
PATCH = 12

W, HEAD_H = 440, 38
TAB_Y, TAB_H, TAB_W = 38, 28, 88
PAGE_Y = 66
COL_GOLD, COL_BG, COL_BTN = 0xE4C36A, 0x0B1020, 0x1A2438
COL_IN, COL_TXT, COL_RED = 0x0A0E18, 0xF3EAD6, 0x6B2A2A
COL_HEAD, COL_GIVE = 0x16120A, 0x3A5A2A
COL_TAB = 0x243044

L_MENU, L_TMP, L_FMT = 4, 5, 6
L_MX, L_MY, L_DOWN, L_PL = 7, 8, 9, 10
L_TGT, L_PAGE, L_CNT = 11, 12, 13
NLOCAL = 14


class A(Asm):
    """Assembler that tracks stack depth and definite assignment."""

    def __init__(self):
        super().__init__()
        self.stack = 0
        self.max_used = 0
        self.reachable = True
        self.stack_at = {}
        self.pending = {}

    def _use(self, d):
        if not self.reachable:
            return
        self.stack += d
        if self.stack < 0:
            raise RuntimeError(f"stack underflow at {len(self.code):#x}")
        self.max_used = max(self.max_used, self.stack)

    def label(self, name):
        if self.reachable and name in self.pending and self.pending[name] != self.stack:
            raise RuntimeError(
                f"label {name}: fallthrough stack {self.stack} != jump stack {self.pending[name]}"
            )
        if name in self.pending:
            if not self.reachable:
                self.stack = self.pending[name]
            self.reachable = True
        elif not self.reachable:
            self.stack = self.pending.get(name, 0)
            self.reachable = True
        self.stack_at[name] = self.stack
        self.labels[name] = len(self.code)

    def _jump_stack(self, lab, after_pop):
        st = after_pop
        if lab in self.pending and self.pending[lab] != st:
            raise RuntimeError(f"jump {lab}: stack {st} vs {self.pending[lab]}")
        self.pending[lab] = st

    def jump(self, opc, lab):
        super().jump(opc, lab)

    def jump_to(self, lab):
        if self.reachable:
            self._jump_stack(lab, self.stack)
        super().jump_to(lab)
        self.reachable = False

    def iffalse(self, lab):
        self._use(-1)
        self._jump_stack(lab, self.stack)
        super().iffalse(lab)

    def iftrue(self, lab):
        self._use(-1)
        self._jump_stack(lab, self.stack)
        super().iftrue(lab)

    def ifeq(self, lab):
        self._use(-2)
        self._jump_stack(lab, self.stack)
        super().ifeq(lab)

    def ifne(self, lab):
        self._use(-2)
        self._jump_stack(lab, self.stack)
        super().ifne(lab)

    def iflt(self, lab):
        self._use(-2)
        self._jump_stack(lab, self.stack)
        self.jump(0x15, lab)

    def ifge(self, lab):
        self._use(-2)
        self._jump_stack(lab, self.stack)
        self.jump(0x18, lab)

    def ifgt(self, lab):
        self._use(-2)
        self._jump_stack(lab, self.stack)
        self.jump(0x17, lab)

    def ifle(self, lab):
        self._use(-2)
        self._jump_stack(lab, self.stack)
        self.jump(0x16, lab)

    def getlocal0(self):
        super().getlocal0()
        self._use(1)

    def getlocal(self, n):
        super().getlocal(n)
        self._use(1)

    def setlocal(self, n):
        self._use(-1)
        super().setlocal(n)

    def pushbyte(self, n):
        super().pushbyte(n)
        self._use(1)

    def pushshort(self, n):
        super().pushshort(n)
        self._use(1)

    def pushstring(self, idx):
        super().pushstring(idx)
        self._use(1)

    def pushnull(self):
        super().pushnull()
        self._use(1)

    def pushundefined(self):
        super().pushundefined()
        self._use(1)

    def pushtrue(self):
        super().pushtrue()
        self._use(1)

    def pushfalse(self):
        super().pushfalse()
        self._use(1)

    def pop(self):
        self._use(-1)
        super().pop()

    def dup(self):
        super().dup()
        self._use(1)

    def getlex(self, mn):
        super().getlex(mn)
        self._use(1)

    def getproperty(self, mn):
        super().getproperty(mn)  # obj -> val

    def getproperty_l(self, mn):
        """MultinameL / runtime index: (obj, idx) -> val."""
        self._use(-1)
        super().getproperty(mn)

    def setproperty(self, mn):
        self._use(-2)
        super().setproperty(mn)

    def setproperty_l(self, mn):
        """MultinameL: (obj, name, val) -> empty."""
        self._use(-1)
        self.setproperty(mn)

    def callproperty(self, mn, argc):
        self._use(-argc)  # obj+args -> result
        super().callproperty(mn, argc)

    def callproperty_l(self, mn, argc):
        """MultinameL: (obj, name, args...) -> result."""
        self._use(-1)
        self.callproperty(mn, argc)

    def callpropvoid(self, mn, argc):
        self._use(-(argc + 1))
        super().callpropvoid(mn, argc)

    def findpropstrict(self, mn):
        super().findpropstrict(mn)
        self._use(1)

    def constructprop(self, mn, argc):
        self._use(-argc)  # obj+args -> instance
        super().constructprop(mn, argc)

    def convert_i(self):
        super().convert_i()

    def convert_d(self):
        super().convert_d()

    def convert_b(self):
        super().convert_b()

    def convert_s(self):
        super().convert_s()

    def coerce_a(self):
        super().coerce_a()

    def add(self):
        self._use(-1)
        super().add()

    def lshift(self):
        self._use(-1)
        super().lshift()

    def divide(self):
        self._use(-1)
        super().divide()

    def multiply(self):
        self._use(-1)
        self.op(0xA2)

    def subtract(self):
        self._use(-1)
        self.op(0xA1)

    def increment(self):
        self.op(0x91)

    def decrement(self):
        self.op(0x93)

    def not_(self):
        super().not_()

    def pushscope(self):
        self._use(-1)
        self.op(0x30)

    def returnvoid(self):
        super().returnvoid()
        self.reachable = False

    def finish(self) -> bytes:
        if self.stack != 0 and self.reachable:
            raise RuntimeError(f"finish stack={self.stack}")
        return super().finish()


def find_game_preupdate(abc: Abc):
    """SurvivalGame.preUpdate is the method GameProcess actually calls."""
    for inst in abc.instances:
        if inst["name"] == "orion.games::SurvivalGame":
            for t in inst["traits"]:
                if t.get("slot") == "method" and t["mn"].split("::")[-1] == "preUpdate":
                    return t["method"]
    raise SystemExit("orion.games::SurvivalGame.preUpdate not found")


def find_core_onenterframe(abc: Abc):
    for inst in abc.instances:
        if inst["name"] == "orion::Core":
            for tr in inst["traits"]:
                if tr.get("slot") == "method" and tr["mn"].split("::")[-1] == "onEnterFrame":
                    return tr["method"]
    raise SystemExit("orion::Core.onEnterFrame not found")


def patch_core_timestep(abc_bytes: bytes) -> bytes:
    """Keep 60 Hz physics when Stage.frameRate is 120.

    Core.onEnterFrame always runs one fixedUpdate per ENTER_FRAME, then more
    while elapsed >= FRAME_RATE (1000/60 ms). Raising frameRate therefore
    doubles world speed. Same-length patch:
    - prevFixed = now (drop ceil snap that fights high FPS)
    - skip the unconditional first tick; the existing while does 60 Hz
    """
    abc = Abc(abc_bytes)
    mid = find_core_onenterframe(abc)
    code = bytearray(abc.bodies[mid])
    snap = bytes.fromhex("d0609c61d2609f3ea3469d6101609f3ea268d103")
    first = bytes.fromhex("d3609f3ea173d7d0609f3e4fe30301")
    if code[33:53] != snap:
        raise RuntimeError(f"onEnterFrame snap {code[33:53].hex()}")
    if code[66:81] != first:
        raise RuntimeError(f"onEnterFrame first tick {code[66:81].hex()}")
    code[33:53] = bytes.fromhex("d0d268d103") + b"\x02" * 15
    code[66:81] = b"\x02" * 15
    meta = abc.body_meta[mid]
    out = bytearray(abc_bytes)
    out[meta["code_off"] : meta["code_off"] + len(code)] = code
    print(f"    Core.onEnterFrame 60Hz physics (len={len(code)})")
    return bytes(out)


def find_preloader_showerror(abc: Abc):
    for inst in abc.instances:
        if inst["name"] == "Preloader":
            for t in inst["traits"]:
                if t.get("slot") == "method" and t["mn"].split("::")[-1] == "showError":
                    return t["method"]
    raise SystemExit("Preloader.showError not found")


def build_show_error(abc: Abc) -> bytes:
    """Keep original showError UI. Replace Error(e).errorID with String(e).

    Original bytes at 63..73: findpropstrict Error; getlocal1; callproperty Error,1;
    getproperty errorID — that constructs a NEW Error with id 0.
    Same-length patch: getlocal1, convert_s, nops. Jumps stay valid.
    """
    mid = find_preloader_showerror(abc)
    code = bytearray(abc.bodies[mid])
    old = bytes.fromhex("5dcb02d146cb020166d102")
    if code[63:74] != old:
        raise RuntimeError(f"showError@63 unexpected {code[63:74].hex()}")
    code[63:74] = bytes.fromhex("d170") + b"\x02" * 9
    print(f"    showError surgical String(e) len={len(code)}")
    return bytes(code)


def hit(a: A, mx, my, x, y, w, h, miss):
    a.getlocal(mx)
    a.pushshort(x)
    a.iflt(miss)
    a.getlocal(mx)
    a.pushshort(x + w)
    a.ifge(miss)
    a.getlocal(my)
    a.pushshort(y)
    a.iflt(miss)
    a.getlocal(my)
    a.pushshort(y + h)
    a.ifge(miss)


def patch_fps_header(data: bytes, fps: int) -> bytes:
    pos = parse_rect(data, 8)
    out = bytearray(data)
    out[pos : pos + 2] = struct.pack("<H", int(fps * 256) & 0xFFFF)
    return bytes(out)


def apply_body_patch(abc_bytes, abc, orig_s, orig_ns, orig_mn, mid, new_code, max_stack, local_count):
    meta = dict(abc.body_meta[mid])
    prefix = abc.rebuild_prefix(orig_s, orig_ns, orig_mn)
    tail = abc_bytes[abc.tail_off :]
    shift = len(prefix) - abc.tail_off
    new_abc = bytearray(prefix + tail)

    def patch_u30_field(orig_off, new_val):
        off = orig_off + shift
        _, after = u30(bytes(new_abc), off)
        old_len = after - off
        new_enc = enc_u30(new_val)
        new_abc[off : off + old_len] = new_enc
        return len(new_enc) - old_len

    d1 = patch_u30_field(meta["max_stack_off"], max(meta["max_stack"], max_stack))
    meta["local_count_off"] += d1
    meta["code_len_off"] += d1
    meta["code_off"] += d1
    d2 = patch_u30_field(meta["local_count_off"], max(meta["local_count"], local_count))
    meta["code_len_off"] += d2
    meta["code_off"] += d2
    d3 = patch_u30_field(meta["code_len_off"], len(new_code))
    meta["code_off"] += d3
    code_off = meta["code_off"] + shift
    new_abc[code_off : code_off + meta["code_len"]] = new_code
    return bytes(new_abc)


def assert_abc_ok(abc_bytes: bytes, expect_instances: int):
    a = Abc(abc_bytes)
    if len(a.instances) != expect_instances:
        raise RuntimeError(f"instances {len(a.instances)} != {expect_instances} (ABC corrupted)")
    if len(a.bodies) < 1000:
        raise RuntimeError("too few method bodies")
    upd = find_game_update(a)
    if a.body_meta[upd]["code_len"] != 333:
        raise RuntimeError(f"Game.update was modified ({a.body_meta[upd]['code_len']}), lobby will break")
    # empty Game.preUpdate must stay empty — SurvivalGame overrides it
    for inst in a.instances:
        if inst["name"] == "orion::Game":
            for t in inst["traits"]:
                if t.get("slot") == "method" and t["mn"].split("::")[-1] == "preUpdate":
                    if a.body_meta[t["method"]]["code_len"] != 3:
                        raise RuntimeError("orion::Game.preUpdate was modified")
    pre = find_game_preupdate(a)
    if a.body_meta[pre]["code_len"] < 400:
        raise RuntimeError("SurvivalGame.preUpdate body too small")
    return a


def build_code(abc: Abc, orig_code: bytes) -> bytes:
    def q(ns, name):
        return abc.find_qname(ns, name) or abc.intern_qname(ns, name)

    def n(name):
        """Prefer public QName (kind 0x07) — MultinameL has extra stack slots (Error #1030)."""
        for i, (kind, nsi, namei, nset, extra) in enumerate(abc.multinames, start=1):
            if kind == 0x07 and namei and abc.str_at(namei) == name:
                return i
        return abc.find_name_any(name) or abc.intern_qname("", name)

    MC = q("flash.display", "MovieClip")
    TF = q("flash.text", "TextField")
    FMT = q("flash.text", "TextFormat")
    TFT = q("flash.text", "TextFieldType")
    INPUT = n("INPUT")
    graphics, beginFill, endFill = n("graphics"), n("beginFill"), n("endFill")
    drawRect, drawRoundRect = n("drawRect"), n("drawRoundRect")
    addChild = n("addChild")
    startDrag, stopDrag = n("startDrag"), n("stopDrag")
    visible, name_mn, text_mn = n("visible"), n("name"), n("text")
    type_mn, border = n("type"), n("border")
    bgColor = n("backgroundColor")
    selectable, mouseEnabled = n("selectable"), n("mouseEnabled")
    defaultTextFormat, embedFonts = n("defaultTextFormat"), n("embedFonts")
    font_mn, size_mn, color_mn = n("font"), n("size"), n("color")
    width_mn, height_mn, x_mn, y_mn = n("width"), n("height"), n("x"), n("y")
    mouseX, mouseY = n("mouseX"), n("mouseY")
    restrict, maxChars = n("restrict"), n("maxChars")
    stage_mn = n("stage")
    player_mn, world_mn, inventory_mn = n("player"), n("world"), n("inventory")
    health_mn = n("health")
    max_health_mn = abc.find_qname("orion.worlds.entities", "maxHealth") or n("maxHealth")
    immortal_mn, level_mn, speed_mn = n("immortal"), n("level"), n("moveSpeed")
    items_mn = n("ITEMS")
    item_cls, stack_cls = q("orion.worlds", "Item"), q("orion.worlds", "ItemStack")
    add_mn, count_mn = n("add"), n("count")
    set_time, add_creature = n("setGameTime"), n("addCreature")
    pos_mn = n("position")
    input_mn, keyDown, mouseDown = n("input"), n("keyDown"), n("mouseDown")
    BMP = q("flash.display", "Bitmap")
    images_cls = q("assets", "Images")
    mobs_icons_mn = n("MOBS_ICONS")
    bitmapData_mn = n("bitmapData")
    smoothing_mn = n("smoothing")
    max_stack_mn = n("maxStackSize")
    hand_idx_mn = n("itemInHandIndex")
    length_mn = n("length")
    controller_mn = n("controller")
    set_sel_mn = n("setSelectedItemIndex")
    inv_items_mn = n("items")
    item_prop_mn = n("item")
    star_mn = 0
    for i, (kind, nsi, namei, nset, extra) in enumerate(abc.multinames, start=1):
        if kind in (0x1B, 0x1C):
            star_mn = i
            break
    if not star_mn:
        raise RuntimeError("no MultinameL for Item.ITEMS[id]")

    def mn_of(path):
        ns, _, nm = path.rpartition("::")
        return abc.find_qname(ns, nm)

    # (label, class path or None) — Enhanced left/right pairs, then extras / original / misc
    U = "orion.worlds.entities.mobs.unique::"
    M = "orion.worlds.entities.mobs::"
    enh_pairs = [
        ("Древний Страж", U + "UGargoyleEntity", None, None),
        ("Король Москитон", U + "UBigSpiderEntity", "Призрак Москитона", U + "UBigShadowSpiderEntity"),
        ("Тиран", U + "UZombieEntity", "Призрак Тирана", U + "UShadowZombieEntity"),
        ("Император Финалиум", U + "UfoEntity", "Призрак Императора", U + "UfoShadowEntity"),
    ]
    enh_extra = [
        ("Свергнутый Король", U + "UGnomeEntity"),
        ("Страж (машина)", U + "UTransformerEntity"),
    ]
    orig_bosses = [
        ("Горгулья", U + "UGargoyleEntity"),
        ("Огромный паук", U + "UBigSpiderEntity"),
        ("Зартан", U + "UZombieEntity"),
        ("Огненный голем", U + "UStoneGolemEntity"),
    ]
    misc_mobs = [
        ("Пламенный Демон", M + "DemonEntity"),
        ("Злой мех. голем", M + "MechanicalGolemEvilEntity"),
        ("Пламенный Робот", M + "FireRobotEntity"),
        ("Злой большой жук", M + "EvilBigSpiderEntity"),
        ("Злой кам. голем", M + "StoneGolemEvilEntity"),
    ]

    catalog_bosses = [
        ("Древний Страж", U + "UGargoyleEntity", "gargoyle"),
        ("Король Москитон", U + "UBigSpiderEntity", "u_big_spider"),
        ("Тиран", U + "UZombieEntity", "u_zombie"),
        ("Император", U + "UfoEntity", "u_big_ufo"),
        ("Призрак Москитона", U + "UBigShadowSpiderEntity", "u_big_shadow_spider"),
        ("Призрак Тирана", U + "UShadowZombieEntity", "u_shadow_zombie"),
        ("Призрак Императора", U + "UfoShadowEntity", "u_big_shadow_ufo"),
        ("Свергнутый Король", U + "UGnomeEntity", "u_gnome"),
        ("Страж (машина)", U + "UTransformerEntity", "robot_transformer"),
        ("Горгулья", U + "UGargoyleEntity", "gargoyle"),
        ("Огненный голем", U + "UStoneGolemEntity", "u_stone_golem"),
    ]
    catalog_mobs = [
        ("Заяц", M + "HareEntity", "hare"),
        ("Курица", M + "ChickenEntity", "chicken_1"),
        ("Овца", M + "SheepEntity", "sheep"),
        ("Койот", M + "CoyoteEntity", "coyote_brown"),
        ("Серый койот", M + "CoyoteGrayEntity", "coyote_gray"),
        ("Зомби", M + "ZombieEntity", "zombie"),
        ("Огненный зомби", M + "FireZombieEntity", "zombie_fire"),
        ("Бронезомби", M + "ArmoredZombieEntity", "armored_zombie"),
        ("Плевок зомби", M + "SpitZombieEntity", "spit_zombie"),
        ("Паук", M + "SpiderEntity", "spider"),
        ("Крохотный паук", M + "TinySpiderEntity", "spider_tiny"),
        ("Большой паук", M + "BigSpiderEntity", "big_spider"),
        ("Гном-воин", M + "GnomeWarriorEntity", "gnome_warrior_1"),
        ("Гном-шахтёр", M + "GnomeMinerEntity", "gnome_miner"),
        ("Гном-алхимик", M + "GnomeAlchemistEntity", "gnome_alchemist"),
        ("Безумный гном", M + "MadGnomeEntity", "mad_gnome"),
        ("Феник", M + "FenicEntity", "fenic"),
        ("Медуза", M + "MeduseEntity", "meduse"),
        ("Рыба", M + "FishEntity", "fish"),
        ("Огненный слизень", M + "FireBlobEntity", "fireblob"),
        ("Огненный червь", M + "FireWormEntity", "fire_worm"),
        ("Скелет-лучник", M + "SkeletonArcherEntity", "skeleton_archer"),
        ("Демон", M + "DemonEntity", "demon"),
        ("Огненный робот", M + "FireRobotEntity", "fire_robot"),
        ("Пёс-страж", M + "BigDogGuardEntity", "big_dog"),
        ("Мех. голем", M + "MechanicalGolemGuardEntity", "mech_golem_good"),
        ("Злой мех. голем", M + "MechanicalGolemEvilEntity", "mech_golem_evil"),
    ]

    s_godOn = abc.intern_string("godOn")
    s_keyWas = abc.intern_string("keyWas")
    s_mdWas = abc.intern_string("mdWas")
    s_dragging = abc.intern_string("dragging")
    s_fpsSet = abc.intern_string("fpsSet")
    s_page = abc.intern_string("page")
    s_pg0 = abc.intern_string("pg0")
    s_pg1 = abc.intern_string("pg1")
    s_pg2 = abc.intern_string("pg2")
    s_pg3 = abc.intern_string("pg3")
    s_pg4 = abc.intern_string("pg4")
    s_tabHi = abc.intern_string("tabHi")
    s_itemPage = abc.intern_string("itemPage")
    s_holdId = abc.intern_string("holdId")
    s_holdT = abc.intern_string("holdT")
    s_holdN = abc.intern_string("holdN")
    s_tfQty = abc.intern_string("tfQty")
    s_tfBQty = abc.intern_string("tfBQty")
    s_tfMQty = abc.intern_string("tfMQty")
    s_tfMobId = abc.intern_string("tfMobId")
    s_tfMobN = abc.intern_string("tfMobN")
    s_tfIName = abc.intern_string("tfIName")
    s_tfLvl = abc.intern_string("tfLvl")
    s_tfHp = abc.intern_string("tfHp")
    s_tfSpd = abc.intern_string("tfSpd")
    s_tfItem = abc.intern_string("tfItem")
    s_tfCnt = abc.intern_string("tfCnt")
    s_tfH = abc.intern_string("tfH")
    s_tfM = abc.intern_string("tfM")
    s_tfFps = abc.intern_string("tfFps")
    s_tfGod = abc.intern_string("tfGod")
    s_tfLight = abc.intern_string("tfLight")
    s_getChildByName = abc.intern_string("getChildByName")
    s_frameRate = abc.intern_string("frameRate")

    s_name = abc.intern_string("orionDev")
    s_sans = abc.intern_string("_sans")
    s_title = abc.intern_string("ORION  ·  Меню")
    s_hint = abc.intern_string("Insert / F7 — меню   ·   тащи за шапку")
    s_tab0 = abc.intern_string("Быстрое")
    s_tab1 = abc.intern_string("Предметы")
    s_tab2 = abc.intern_string("Боссы")
    s_tab3 = abc.intern_string("Мобы")
    s_tab4 = abc.intern_string("Опции")
    s_qty = abc.intern_string("× спавн")
    s_mobid = abc.intern_string("ID моба")
    s_spawn = abc.intern_string("Спавн")
    s_prev = abc.intern_string("<")
    s_next = abc.intern_string(">")
    s_iclick = abc.intern_string("Клик — 1 в руку. Зажать — стак быстрее. Не стакается — только 1")
    s_bclick = abc.intern_string("Число, потом клик по боссу — столько рядом")
    s_mclick = abc.intern_string("Число, потом клик по мобу — столько рядом")
    s_ib = abc.intern_string("ib")
    s_iid = abc.intern_string("iid")
    s_lvl = abc.intern_string("Уровень")
    s_hp = abc.intern_string("Здоровье")
    s_god_off = abc.intern_string("Режим бога: ВЫКЛ  (нажми)")
    s_god_on = abc.intern_string("Режим бога: ВКЛ  (нажми)")
    s_spd = abc.intern_string("Скорость")
    s_item = abc.intern_string("ID предмета")
    s_cnt = abc.intern_string("Кол-во")
    s_ok = abc.intern_string("OK")
    s_give = abc.intern_string("Выдать")
    s_enh = abc.intern_string("Орион Улучшенный  ·  слева обычный, справа призрак")
    s_orig = abc.intern_string("Орион (первый)")
    s_misc = abc.intern_string("Прочие")
    s_h = abc.intern_string("ч")
    s_min = abc.intern_string("мин")
    s_morn = abc.intern_string("Утро")
    s_day = abc.intern_string("День")
    s_eve = abc.intern_string("Вечер")
    s_night = abc.intern_string("Ночь")
    s_set = abc.intern_string("Experimental")
    s_fpsl = abc.intern_string("FPS")
    s_gfx = abc.intern_string("Картинка 10–240 FPS, мир всегда 60. 120 — плавнее")
    s_help = abc.intern_string("34 кирка  36 жел.кирка  42 зол.меч  43 обс.меч  64 зелье  138 Громон")
    s_light = abc.intern_string("Свет")
    s_light0 = abc.intern_string("Как в игре")
    s_light1 = abc.intern_string("Не сквозь стены")
    s_light_h = abc.intern_string("Факел — круг в Lighting.addCircleLight, стены не гасят")
    s_move_h = abc.intern_string("Прыжок/ход: физика 60 Гц. Выше FPS в Настройках — меньше рывков кадра")
    s_1 = abc.intern_string("1")
    s_100 = abc.intern_string("100")
    s_2 = abc.intern_string("2")
    s_12 = abc.intern_string("12")
    s_0 = abc.intern_string("0")
    s_120 = abc.intern_string("120")
    s_restrict = abc.intern_string("0-9")
    pair_labs = []
    for left, _lp, right, _rp in enh_pairs:
        pair_labs.append(abc.intern_string(left[:22]))
        pair_labs.append(abc.intern_string(right[:22]) if right else 0)
    extra_labs = [abc.intern_string(n[:22]) for n, _ in enh_extra]
    orig_labs = [abc.intern_string(n[:22]) for n, _ in orig_bosses]
    misc_labs = [abc.intern_string(n[:22]) for n, _ in misc_mobs]
    cb_labs = [abc.intern_string(n[:18]) for n, _, _ in catalog_bosses]
    cb_keys = [abc.intern_string(k) for _, _, k in catalog_bosses]
    cm_labs = [abc.intern_string(n[:16]) for n, _, _ in catalog_mobs]
    cm_keys = [abc.intern_string(k) for _, _, k in catalog_mobs]
    s_bmps = [abc.intern_string(f"ib{i}") for i in range(40)]
    s_bbmp = [abc.intern_string(f"bb{i}") for i in range(len(catalog_bosses))]
    s_mbmp = [abc.intern_string(f"mb{i}") for i in range(len(catalog_mobs))]

    H = 660
    # page-local Y (add PAGE_Y for menu-space hit tests)
    Y_LVL, Y_HP, Y_GOD, Y_SPD = 8, 40, 72, 104
    Y_ITEM, Y_HELP = 136, 168
    Y_ENH, Y_EPAIR = 190, 210
    Y_EEXTRA = 210 + 4 * 26
    Y_ORIG, Y_OPAIR = Y_EEXTRA + 34, Y_EEXTRA + 54
    Y_MISC, Y_MROW = Y_OPAIR + 60, Y_OPAIR + 80
    Y_TIME, Y_HM = Y_MROW + 60, Y_MROW + 90
    Y_FPS, Y_GFX = 48, 80
    Y_LIGHT, Y_L0, Y_LH = 8, 40, 76
    Y_MOVE = 120
    Y_QTY = Y_ENH
    Y_MOBSP = Y_HM + 36
    IC, IR, ICS = 10, 4, 40
    IGX, IGY = 20, 52
    OK_X, OK_W, IN_X, IN_W = 340, 84, 118, 210
    BW, BH = 200, 24
    LX, RX = 16, 224

    a = A()

    def push_color(col):
        """RGB as int without pushshort > 32767 (AVM2 pushshort is 16-bit)."""
        col &= 0xFFFFFF
        a.pushshort((col >> 16) & 0xFF)
        a.pushbyte(16)
        a.lshift()
        a.pushshort((col >> 8) & 0xFF)
        a.pushbyte(8)
        a.lshift()
        a.add()
        a.pushshort(col & 0xFF)
        a.add()

    def dget(sidx):
        a.getlocal(L_MENU)
        a.pushstring(sidx)
        a.getproperty_l(star_mn)

    def dset_false(sidx):
        a.getlocal(L_MENU)
        a.pushstring(sidx)
        a.pushfalse()
        a.setproperty_l(star_mn)

    def dset_true(sidx):
        a.getlocal(L_MENU)
        a.pushstring(sidx)
        a.pushtrue()
        a.setproperty_l(star_mn)

    def dset_local(sidx, loc):
        a.getlocal(L_MENU)
        a.pushstring(sidx)
        a.getlocal(loc)
        a.setproperty_l(star_mn)

    a.getlocal0()
    a.pushscope()

    # ASC typecheck: extra locals start as undefined + coerce_a.
    for i in range(2, NLOCAL):
        a.pushundefined()
        a.coerce_a()
        a.setlocal(i)

    a.getlocal0()
    a.getproperty(stage_mn)
    a.pushnull()
    a.ifeq("do_orig")

    a.getlocal0()
    a.getproperty(stage_mn)
    a.pushstring(s_getChildByName)
    a.pushstring(s_name)
    a.callproperty_l(star_mn, 1)
    a.coerce_a()
    a.setlocal(L_MENU)
    a.getlocal(L_MENU)
    a.pushnull()
    a.ifne("inited")

    a.findpropstrict(MC)
    a.constructprop(MC, 0)
    a.setlocal(L_MENU)
    a.getlocal(L_MENU)
    a.pushstring(s_name)
    a.setproperty(name_mn)
    a.getlocal(L_MENU)
    a.pushshort(36)
    a.setproperty(x_mn)
    a.getlocal(L_MENU)
    a.pushshort(48)
    a.setproperty(y_mn)
    a.getlocal(L_MENU)
    a.pushfalse()
    a.setproperty(visible)
    for prop in (s_godOn, s_keyWas, s_dragging, s_fpsSet):
        dset_false(prop)
    # leftover mouseDown from «войти в мир» must not be a menu click
    dset_true(s_mdWas)
    a.getlocal(L_MENU)
    a.pushstring(s_page)
    a.pushbyte(0)
    a.setproperty_l(star_mn)
    for prop, val in ((s_itemPage, 0), (s_holdId, 255), (s_holdT, 0), (s_holdN, 0)):
        a.getlocal(L_MENU)
        a.pushstring(prop)
        a.pushbyte(val)
        a.setproperty_l(star_mn)

    def fill(color, x, y, w, h, rnd=0, tgt=None):
        if tgt is None:
            tgt = L_MENU
        a.getlocal(tgt)
        a.getproperty(graphics)
        push_color(color)
        a.callpropvoid(beginFill, 1)
        a.getlocal(tgt)
        a.getproperty(graphics)
        a.pushshort(x)
        a.pushshort(y)
        a.pushshort(w)
        a.pushshort(h)
        if rnd:
            a.pushbyte(rnd)
            a.pushbyte(rnd)
            a.callpropvoid(drawRoundRect, 6)
        else:
            a.callpropvoid(drawRect, 4)
        a.getlocal(tgt)
        a.getproperty(graphics)
        a.callpropvoid(endFill, 0)

    def make_page(store, shown):
        a.findpropstrict(MC)
        a.constructprop(MC, 0)
        a.setlocal(L_TGT)
        a.getlocal(L_TGT)
        a.pushshort(0)
        a.setproperty(x_mn)
        a.getlocal(L_TGT)
        a.pushshort(PAGE_Y)
        a.setproperty(y_mn)
        a.getlocal(L_TGT)
        if shown:
            a.pushtrue()
        else:
            a.pushfalse()
        a.setproperty(visible)
        a.getlocal(L_MENU)
        a.getlocal(L_TGT)
        a.callpropvoid(addChild, 1)
        dset_local(store, L_TGT)

    fill(COL_BG, 0, 0, W, H, 12)
    fill(COL_HEAD, 0, 0, W, HEAD_H, 0)
    fill(COL_TAB, 0, TAB_Y, W, TAB_H, 0)

    a.findpropstrict(MC)
    a.constructprop(MC, 0)
    a.setlocal(L_TMP)
    a.getlocal(L_TMP)
    a.getproperty(graphics)
    push_color(COL_GOLD)
    a.callpropvoid(beginFill, 1)
    a.getlocal(L_TMP)
    a.getproperty(graphics)
    a.pushbyte(0)
    a.pushbyte(0)
    a.pushshort(TAB_W)
    a.pushshort(TAB_H)
    a.callpropvoid(drawRect, 4)
    a.getlocal(L_TMP)
    a.getproperty(graphics)
    a.callpropvoid(endFill, 0)
    a.getlocal(L_TMP)
    a.pushbyte(0)
    a.setproperty(x_mn)
    a.getlocal(L_TMP)
    a.pushshort(TAB_Y)
    a.setproperty(y_mn)
    a.getlocal(L_MENU)
    a.getlocal(L_TMP)
    a.callpropvoid(addChild, 1)
    dset_local(s_tabHi, L_TMP)

    make_page(s_pg0, True)
    # L_TGT is page 0 — cheats chrome
    fill(COL_BTN, OK_X, Y_LVL, OK_W, 24, 6, L_TGT)
    fill(COL_BTN, OK_X, Y_HP, OK_W, 24, 6, L_TGT)
    fill(COL_RED, 16, Y_GOD, W - 32, 26, 6, L_TGT)
    fill(COL_BTN, OK_X, Y_SPD, OK_W, 24, 6, L_TGT)
    fill(COL_GIVE, OK_X, Y_ITEM, OK_W, 24, 6, L_TGT)
    fill(COL_BTN, OK_X, Y_HM, OK_W, 24, 6, L_TGT)
    fill(COL_BTN, 270, Y_QTY, 50, 22, 4, L_TGT)
    fill(COL_GIVE, OK_X, Y_MOBSP, OK_W, 24, 6, L_TGT)
    for xx in (16, 122, 228, 334):
        fill(COL_BTN, xx, Y_TIME, 90, 24, 6, L_TGT)
    for i, (_l, _lp, right, _rp) in enumerate(enh_pairs):
        fill(COL_BTN, LX, Y_EPAIR + i * 26, BW, BH, 5, L_TGT)
        if right:
            fill(COL_BTN, RX, Y_EPAIR + i * 26, BW, BH, 5, L_TGT)
    for i in range(len(enh_extra)):
        fill(COL_BTN, LX + i * 208, Y_EEXTRA, BW, BH, 5, L_TGT)
    for i in range(len(orig_bosses)):
        fill(COL_BTN, LX + (i % 2) * 208, Y_OPAIR + (i // 2) * 26, BW, BH, 5, L_TGT)
    for i in range(len(misc_mobs)):
        fill(COL_BTN, 16 + (i % 3) * 140, Y_MROW + (i // 3) * 26, 132, BH, 5, L_TGT)

    make_page(s_pg1, False)
    fill(COL_BTN, 16, 8, 36, 24, 4, L_TGT)
    fill(COL_BTN, 60, 8, 36, 24, 4, L_TGT)

    make_page(s_pg2, False)

    make_page(s_pg3, False)

    make_page(s_pg4, False)
    fill(COL_BTN, 16, Y_L0, 200, 28, 6, L_TGT)
    fill(COL_BTN, 224, Y_L0, 200, 28, 6, L_TGT)
    fill(COL_BTN, OK_X, 208, OK_W, 24, 6, L_TGT)

    a.findpropstrict(FMT)
    a.constructprop(FMT, 0)
    a.setlocal(L_FMT)
    a.getlocal(L_FMT)
    a.pushstring(s_sans)
    a.setproperty(font_mn)
    a.getlocal(L_FMT)
    a.pushbyte(13)
    a.setproperty(size_mn)
    a.getlocal(L_FMT)
    push_color(COL_TXT)
    a.setproperty(color_mn)

    def add_tf(text_idx, x, y, w, h, store=None, inp=False, def_text=None, parent=None):
        if parent is None:
            parent = L_MENU
        a.findpropstrict(TF)
        a.constructprop(TF, 0)
        a.setlocal(L_TMP)
        a.getlocal(L_TMP)
        a.getlocal(L_FMT)
        a.setproperty(defaultTextFormat)
        a.getlocal(L_TMP)
        a.pushfalse()
        a.setproperty(embedFonts)
        a.getlocal(L_TMP)
        a.pushshort(x)
        a.setproperty(x_mn)
        a.getlocal(L_TMP)
        a.pushshort(y)
        a.setproperty(y_mn)
        a.getlocal(L_TMP)
        a.pushshort(w)
        a.setproperty(width_mn)
        a.getlocal(L_TMP)
        a.pushshort(h)
        a.setproperty(height_mn)
        if inp:
            a.getlocal(L_TMP)
            a.getlex(TFT)
            a.getproperty(INPUT)
            a.setproperty(type_mn)
            a.getlocal(L_TMP)
            a.pushtrue()
            a.setproperty(border)
            a.getlocal(L_TMP)
            push_color(COL_IN)
            a.setproperty(bgColor)
            a.getlocal(L_TMP)
            a.pushtrue()
            a.setproperty(selectable)
            a.getlocal(L_TMP)
            a.pushtrue()
            a.setproperty(mouseEnabled)
            a.getlocal(L_TMP)
            a.pushstring(s_restrict)
            a.setproperty(restrict)
            a.getlocal(L_TMP)
            a.pushbyte(6)
            a.setproperty(maxChars)
            a.getlocal(L_TMP)
            a.pushstring(def_text if def_text is not None else s_1)
            a.setproperty(text_mn)
        else:
            a.getlocal(L_TMP)
            a.pushfalse()
            a.setproperty(selectable)
            a.getlocal(L_TMP)
            a.pushfalse()
            a.setproperty(mouseEnabled)
            a.getlocal(L_TMP)
            a.pushstring(text_idx)
            a.setproperty(text_mn)
        a.getlocal(parent)
        a.getlocal(L_TMP)
        a.callpropvoid(addChild, 1)
        if store:
            dset_local(store, L_TMP)

    add_tf(s_title, 12, 8, 300, 22)
    add_tf(s_hint, 12, H - 20, 420, 18)
    add_tf(s_tab0, 4, TAB_Y + 4, TAB_W - 6, 20)
    add_tf(s_tab1, 4 + TAB_W, TAB_Y + 4, TAB_W - 6, 20)
    add_tf(s_tab2, 4 + TAB_W * 2, TAB_Y + 4, TAB_W - 6, 20)
    add_tf(s_tab3, 4 + TAB_W * 3, TAB_Y + 4, TAB_W - 6, 20)
    add_tf(s_tab4, 4 + TAB_W * 4, TAB_Y + 4, TAB_W - 6, 20)

    dget(s_pg0)
    a.setlocal(L_TGT)
    add_tf(s_lvl, 16, Y_LVL, 100, 22, parent=L_TGT)
    add_tf(s_1, IN_X, Y_LVL, IN_W, 22, store=s_tfLvl, inp=True, def_text=s_1, parent=L_TGT)
    add_tf(s_ok, OK_X + 28, Y_LVL + 2, 60, 20, parent=L_TGT)
    add_tf(s_hp, 16, Y_HP, 100, 22, parent=L_TGT)
    add_tf(s_100, IN_X, Y_HP, IN_W, 22, store=s_tfHp, inp=True, def_text=s_100, parent=L_TGT)
    add_tf(s_ok, OK_X + 28, Y_HP + 2, 60, 20, parent=L_TGT)
    add_tf(s_god_off, 28, Y_GOD + 3, 380, 22, store=s_tfGod, parent=L_TGT)
    add_tf(s_spd, 16, Y_SPD, 100, 22, parent=L_TGT)
    add_tf(s_2, IN_X, Y_SPD, IN_W, 22, store=s_tfSpd, inp=True, def_text=s_2, parent=L_TGT)
    add_tf(s_ok, OK_X + 28, Y_SPD + 2, 60, 20, parent=L_TGT)
    add_tf(s_item, 16, Y_ITEM, 100, 22, parent=L_TGT)
    add_tf(s_1, IN_X, Y_ITEM, 90, 22, store=s_tfItem, inp=True, def_text=s_1, parent=L_TGT)
    add_tf(s_cnt, 214, Y_ITEM, 60, 22, parent=L_TGT)
    add_tf(s_1, 270, Y_ITEM, 60, 22, store=s_tfCnt, inp=True, def_text=s_1, parent=L_TGT)
    add_tf(s_give, OK_X + 10, Y_ITEM + 2, 70, 20, parent=L_TGT)
    add_tf(s_help, 16, Y_HELP, 408, 20, parent=L_TGT)
    add_tf(s_enh, 16, Y_ENH, 408, 18, parent=L_TGT)
    for i, (left, _lp, right, _rp) in enumerate(enh_pairs):
        add_tf(pair_labs[i * 2], LX + 6, Y_EPAIR + i * 26 + 3, BW - 10, 18, parent=L_TGT)
        if right:
            add_tf(pair_labs[i * 2 + 1], RX + 6, Y_EPAIR + i * 26 + 3, BW - 10, 18, parent=L_TGT)
    for i, lab in enumerate(extra_labs):
        add_tf(lab, LX + 6 + i * 208, Y_EEXTRA + 3, BW - 10, 18, parent=L_TGT)
    add_tf(s_orig, 16, Y_ORIG, 400, 18, parent=L_TGT)
    for i, lab in enumerate(orig_labs):
        add_tf(lab, LX + 6 + (i % 2) * 208, Y_OPAIR + (i // 2) * 26 + 3, BW - 10, 18, parent=L_TGT)
    add_tf(s_misc, 16, Y_MISC, 400, 18, parent=L_TGT)
    for i, lab in enumerate(misc_labs):
        add_tf(lab, 20 + (i % 3) * 140, Y_MROW + (i // 3) * 26 + 3, 124, 18, parent=L_TGT)
    add_tf(s_morn, 36, Y_TIME + 3, 70, 18, parent=L_TGT)
    add_tf(s_day, 142, Y_TIME + 3, 70, 18, parent=L_TGT)
    add_tf(s_eve, 244, Y_TIME + 3, 70, 18, parent=L_TGT)
    add_tf(s_night, 348, Y_TIME + 3, 70, 18, parent=L_TGT)
    add_tf(s_h, 16, Y_HM, 24, 22, parent=L_TGT)
    add_tf(s_12, 40, Y_HM, 70, 22, store=s_tfH, inp=True, def_text=s_12, parent=L_TGT)
    add_tf(s_min, 120, Y_HM, 40, 22, parent=L_TGT)
    add_tf(s_0, 160, Y_HM, 70, 22, store=s_tfM, inp=True, def_text=s_0, parent=L_TGT)
    add_tf(s_ok, OK_X + 28, Y_HM + 2, 60, 20, parent=L_TGT)
    add_tf(s_qty, 200, Y_QTY + 2, 68, 18, parent=L_TGT)
    add_tf(s_1, 270, Y_QTY, 50, 22, store=s_tfQty, inp=True, def_text=s_1, parent=L_TGT)
    add_tf(s_mobid, 16, Y_MOBSP, 80, 22, parent=L_TGT)
    add_tf(s_1, 100, Y_MOBSP, 50, 22, store=s_tfMobId, inp=True, def_text=s_1, parent=L_TGT)
    add_tf(s_cnt, 160, Y_MOBSP, 50, 22, parent=L_TGT)
    add_tf(s_1, 214, Y_MOBSP, 50, 22, store=s_tfMobN, inp=True, def_text=s_1, parent=L_TGT)
    add_tf(s_spawn, OK_X + 10, Y_MOBSP + 2, 70, 20, parent=L_TGT)

    dget(s_pg1)
    a.setlocal(L_TGT)
    add_tf(s_prev, 22, 10, 24, 20, parent=L_TGT)
    add_tf(s_next, 66, 10, 24, 20, parent=L_TGT)
    add_tf(s_iclick, 104, 10, 320, 20, store=s_tfIName, parent=L_TGT)

    _bsk = [0]

    def add_bmp(name_s, x, y, w, h, parent, icon_key=None, items_id=None):
        a.findpropstrict(BMP)
        a.constructprop(BMP, 0)
        a.setlocal(L_TMP)
        a.getlocal(L_TMP)
        a.pushstring(name_s)
        a.setproperty(name_mn)
        a.getlocal(L_TMP)
        a.pushshort(x)
        a.setproperty(x_mn)
        a.getlocal(L_TMP)
        a.pushshort(y)
        a.setproperty(y_mn)
        a.getlocal(L_TMP)
        a.pushtrue()
        a.setproperty(smoothing_mn)
        # patch12: width/height НЕ до bitmapData! У пустого Bitmap baseWidth=0
        # и setter width вычисляет scaleX = w/0 = Infinity — в AIR это роняет
        # процесс (p11: закрылось при «старт»). Размер ставится только после
        # присвоения bitmapData (ниже) либо в do_fill (там порядок верный).
        if icon_key is not None:
            skip = f"bsk{_bsk[0]}"
            _bsk[0] += 1
            a.getlex(images_cls)
            a.getproperty(mobs_icons_mn)
            a.pushstring(icon_key)
            a.getproperty_l(star_mn)
            a.coerce_a()
            a.setlocal(L_CNT)
            a.getlocal(L_CNT)
            a.pushnull()
            a.ifeq(skip)
            a.getlocal(L_TMP)
            a.getlocal(L_CNT)
            a.setproperty(bitmapData_mn)
            a.getlocal(L_TMP)
            a.pushshort(w)
            a.setproperty(width_mn)
            a.getlocal(L_TMP)
            a.pushshort(h)
            a.setproperty(height_mn)
            a.label(skip)
        a.getlocal(parent)
        a.getlocal(L_TMP)
        a.callpropvoid(addChild, 1)
        dset_local(name_s, L_TMP)

    for i in range(40):
        add_bmp(s_bmps[i], IGX + (i % IC) * ICS, IGY + (i // IC) * ICS, 36, 36, L_TGT)

    dget(s_pg2)
    a.setlocal(L_TGT)
    add_tf(s_bclick, 16, 8, 250, 20, parent=L_TGT)
    add_tf(s_qty, 270, 8, 60, 20, parent=L_TGT)
    add_tf(s_1, 340, 6, 50, 22, store=s_tfBQty, inp=True, def_text=s_1, parent=L_TGT)
    for i, (_n, _p, _k) in enumerate(catalog_bosses):
        bx, by = 16 + (i % 4) * 106, 36 + (i // 4) * 88
        add_bmp(s_bbmp[i], bx + 24, by, 48, 48, L_TGT, icon_key=cb_keys[i])
        add_tf(cb_labs[i], bx, by + 50, 100, 18, parent=L_TGT)

    dget(s_pg3)
    a.setlocal(L_TGT)
    add_tf(s_mclick, 16, 8, 250, 20, parent=L_TGT)
    add_tf(s_qty, 270, 8, 60, 20, parent=L_TGT)
    add_tf(s_1, 340, 6, 50, 22, store=s_tfMQty, inp=True, def_text=s_1, parent=L_TGT)
    for i, (_n, _p, _k) in enumerate(catalog_mobs):
        mx, my = 12 + (i % 6) * 70, 36 + (i // 6) * 86
        add_bmp(s_mbmp[i], mx + 10, my, 44, 44, L_TGT, icon_key=cm_keys[i])
        add_tf(cm_labs[i], mx, my + 46, 68, 18, parent=L_TGT)

    dget(s_pg4)
    a.setlocal(L_TGT)
    add_tf(s_light, 16, Y_LIGHT, 400, 22, parent=L_TGT)
    add_tf(s_light0, 40, Y_L0 + 4, 160, 20, parent=L_TGT)
    add_tf(s_light1, 244, Y_L0 + 4, 170, 20, parent=L_TGT)
    add_tf(s_light_h, 16, Y_LH, 408, 36, store=s_tfLight, parent=L_TGT)
    add_tf(s_move_h, 16, Y_MOVE, 408, 40, parent=L_TGT)
    add_tf(s_set, 16, 176, 400, 20, parent=L_TGT)
    add_tf(s_fpsl, 16, 208, 100, 22, parent=L_TGT)
    add_tf(s_120, IN_X, 208, IN_W, 22, store=s_tfFps, inp=True, def_text=s_120, parent=L_TGT)
    add_tf(s_ok, OK_X + 28, 210, 60, 20, parent=L_TGT)
    add_tf(s_gfx, 16, 240, 408, 40, parent=L_TGT)

    a.getlocal0()
    a.getproperty(stage_mn)
    a.getlocal(L_MENU)
    a.callpropvoid(addChild, 1)
    a.jump_to("after_init")
    a.label("inited")
    a.label("after_init")

    # EXP1 (2026-09-12): принудительного stage.frameRate=120 нет — стейдж
    # остаётся на 60 FPS (как в patch7). Кнопка FPS на 4-й вкладке работает,
    # но без timestep-патча 120 FPS даст мир в 2 раза быстрее — это чинится
    # отдельно, в patch11 не трогаем цикл таймингов.

    a.getlocal0()
    a.getproperty(player_mn)
    a.coerce_a()
    a.setlocal(L_PL)

    a.getlocal(L_PL)
    a.pushnull()
    a.ifeq("ui_keys")
    dget(s_godOn)
    a.convert_b()
    a.iffalse("ui_keys")
    a.getlocal(L_PL)
    a.pushtrue()
    a.setproperty(immortal_mn)
    a.getlocal(L_PL)
    a.getlocal(L_PL)
    a.getproperty(max_health_mn)
    a.setproperty(health_mn)
    a.label("ui_keys")

    def key_edge(code, was_s, hit_lab, skip_lab):
        a.getlocal0()
        a.getproperty(input_mn)
        a.pushbyte(code)
        a.callproperty(keyDown, 1)
        a.convert_b()
        a.iffalse(skip_lab)
        dget(was_s)
        a.convert_b()
        a.iftrue(skip_lab)
        a.jump_to(hit_lab)
        a.label(skip_lab)

    key_edge(45, s_keyWas, "do_toggle", "no_ins")
    key_edge(118, s_keyWas, "do_toggle", "no_f7")
    a.jump_to("after_toggle")
    a.label("do_toggle")
    a.getlocal(L_MENU)
    a.getlocal(L_MENU)
    a.getproperty(visible)
    a.convert_b()
    a.not_()
    a.setproperty(visible)
    a.getlocal0()
    a.getproperty(stage_mn)
    a.getlocal(L_MENU)
    a.callpropvoid(addChild, 1)
    a.label("after_toggle")
    a.getlocal(L_MENU)
    a.pushstring(s_keyWas)
    a.getlocal0()
    a.getproperty(input_mn)
    a.pushbyte(45)
    a.callproperty(keyDown, 1)
    a.convert_b()
    a.dup()
    a.iftrue("key_held")
    a.pop()
    a.getlocal0()
    a.getproperty(input_mn)
    a.pushbyte(118)
    a.callproperty(keyDown, 1)
    a.convert_b()
    a.label("key_held")
    a.setproperty_l(star_mn)

    a.getlocal(L_MENU)
    a.getproperty(visible)
    a.convert_b()
    a.iffalse("do_orig")

    a.getlocal0()
    a.getproperty(input_mn)
    a.getproperty(mouseDown)
    a.convert_b()
    a.setlocal(L_DOWN)
    a.getlocal(L_MENU)
    a.getproperty(mouseX)
    a.convert_i()
    a.setlocal(L_MX)
    a.getlocal(L_MENU)
    a.getproperty(mouseY)
    a.convert_i()
    a.setlocal(L_MY)

    dget(s_dragging)
    a.convert_b()
    a.iffalse("not_dragging")
    a.getlocal(L_DOWN)
    a.convert_b()
    a.iftrue("keep_drag")
    a.getlocal(L_MENU)
    a.callpropvoid(stopDrag, 0)
    dset_false(s_dragging)
    a.label("keep_drag")
    a.jump_to("after_drag")
    a.label("not_dragging")
    a.label("after_drag")

    a.getlocal(L_DOWN)
    a.convert_b()
    a.iffalse("no_click")
    dget(s_mdWas)
    a.convert_b()
    a.iftrue("no_click")

    hit(a, L_MX, L_MY, 0, 0, W, HEAD_H, "not_head")
    a.getlocal(L_MENU)
    a.callpropvoid(startDrag, 0)
    dset_true(s_dragging)
    a.jump_to("click_done")
    a.label("not_head")

    def apply_page(idx):
        a.getlocal(L_MENU)
        a.pushstring(s_page)
        a.pushbyte(idx)
        a.setproperty_l(star_mn)
        dget(s_tabHi)
        a.pushshort(idx * TAB_W)
        a.setproperty(x_mn)
        for j, s in enumerate((s_pg0, s_pg1, s_pg2, s_pg3, s_pg4)):
            dget(s)
            if j == idx:
                a.pushtrue()
            else:
                a.pushfalse()
            a.setproperty(visible)

    for i, miss in enumerate(("ntab1", "ntab2", "ntab3", "ntab4", "ntab5")):
        hit(a, L_MX, L_MY, i * TAB_W, TAB_Y, TAB_W, TAB_H, miss)
        apply_page(i)
        if i == 1:
            a.jump_to("do_fill")
        else:
            a.jump_to("click_done")
        a.label(miss)

    def need_player(miss):
        a.getlocal(L_PL)
        a.pushnull()
        a.ifeq(miss)

    def read_tf(sidx):
        dget(sidx)
        a.getproperty(text_mn)
        a.convert_i()
        a.setlocal(L_TMP)

    _uid = [0]

    def uid(prefix):
        _uid[0] += 1
        return f"{prefix}{_uid[0]}"

    def clamp_qty(sidx):
        read_tf(sidx)
        lo, hi, ok = uid("cqlo"), uid("cqhi"), uid("cqok")
        a.getlocal(L_TMP)
        a.pushbyte(1)
        a.ifge(hi)
        a.pushbyte(1)
        a.setlocal(L_TMP)
        a.jump_to(ok)
        a.label(hi)
        a.getlocal(L_TMP)
        a.pushbyte(50)
        a.ifle(ok)
        a.pushbyte(50)
        a.setlocal(L_TMP)
        a.label(ok)

    def spawn_one(mn):
        if not mn:
            return
        a.findpropstrict(mn)
        a.getlocal(L_PL)
        a.getproperty(pos_mn)
        a.getproperty(x_mn)
        a.pushbyte(80)
        a.add()
        a.getlocal(L_PL)
        a.getproperty(pos_mn)
        a.getproperty(y_mn)
        a.constructprop(mn, 2)
        a.setlocal(L_FMT)
        a.getlocal0()
        a.getproperty(world_mn)
        a.getlocal(L_FMT)
        a.callpropvoid(add_creature, 1)

    def spawn_n(mn, qty_s, done="click_done"):
        need_player(done)
        if not mn:
            a.jump_to(done)
            return
        clamp_qty(qty_s)
        a.getlocal(L_TMP)
        a.setlocal(L_CNT)
        loop = uid("spl")
        a.label(loop)
        a.getlocal(L_CNT)
        a.pushbyte(1)
        a.iflt(done)
        spawn_one(mn)
        a.getlocal(L_CNT)
        a.decrement()
        a.convert_i()
        a.setlocal(L_CNT)
        a.jump_to(loop)

    def spawn(mn):
        spawn_n(mn, s_tfQty)

    dget(s_page)
    a.convert_i()
    a.setlocal(L_PAGE)
    a.getlocal(L_PAGE)
    a.pushbyte(0)
    a.ifne("skip_cheats")

    hit(a, L_MX, L_MY, OK_X, PAGE_Y + Y_LVL, OK_W, 24, "c_hp")
    need_player("click_done")
    read_tf(s_tfLvl)
    a.getlocal(L_PL)
    a.getlocal(L_TMP)
    a.setproperty(level_mn)
    a.jump_to("click_done")
    a.label("c_hp")

    hit(a, L_MX, L_MY, OK_X, PAGE_Y + Y_HP, OK_W, 24, "c_god")
    need_player("click_done")
    read_tf(s_tfHp)
    a.getlocal(L_PL)
    a.getlocal(L_TMP)
    a.setproperty(health_mn)
    a.jump_to("click_done")
    a.label("c_god")

    hit(a, L_MX, L_MY, 16, PAGE_Y + Y_GOD, W - 32, 26, "c_spd")
    a.getlocal(L_MENU)
    a.pushstring(s_godOn)
    dget(s_godOn)
    a.convert_b()
    a.not_()
    a.setproperty_l(star_mn)
    dget(s_godOn)
    a.convert_b()
    a.iffalse("god_off")
    dget(s_tfGod)
    a.pushstring(s_god_on)
    a.setproperty(text_mn)
    a.getlocal(L_PL)
    a.pushnull()
    a.ifeq("click_done")
    a.getlocal(L_PL)
    a.pushtrue()
    a.setproperty(immortal_mn)
    a.jump_to("click_done")
    a.label("god_off")
    dget(s_tfGod)
    a.pushstring(s_god_off)
    a.setproperty(text_mn)
    a.getlocal(L_PL)
    a.pushnull()
    a.ifeq("click_done")
    a.getlocal(L_PL)
    a.pushfalse()
    a.setproperty(immortal_mn)
    a.jump_to("click_done")
    a.label("c_spd")

    hit(a, L_MX, L_MY, OK_X, PAGE_Y + Y_SPD, OK_W, 24, "c_item")
    need_player("click_done")
    dget(s_tfSpd)
    a.getproperty(text_mn)
    a.convert_d()
    a.setlocal(L_TMP)
    a.getlocal(L_PL)
    a.getlocal(L_TMP)
    a.setproperty(speed_mn)
    a.jump_to("click_done")
    a.label("c_item")

    hit(a, L_MX, L_MY, OK_X, PAGE_Y + Y_ITEM, OK_W, 24, "c_boss")
    a.getlex(item_cls)
    a.getproperty(items_mn)
    dget(s_tfItem)
    a.getproperty(text_mn)
    a.convert_i()
    a.getproperty_l(star_mn)
    a.coerce_a()
    a.setlocal(L_FMT)
    a.getlocal(L_FMT)
    a.pushnull()
    a.ifeq("click_done")
    read_tf(s_tfCnt)
    a.getlocal0()
    a.getproperty(inventory_mn)
    a.findpropstrict(stack_cls)
    a.getlocal(L_FMT)
    a.constructprop(stack_cls, 1)
    a.dup()
    a.getlocal(L_TMP)
    a.setproperty(count_mn)
    a.callpropvoid(add_mn, 1)
    a.jump_to("click_done")
    a.label("c_boss")

    bidx = 0
    for i, (_l, lp, right, rp) in enumerate(enh_pairs):
        miss = f"nb{bidx}"
        bidx += 1
        hit(a, L_MX, L_MY, LX, PAGE_Y + Y_EPAIR + i * 26, BW, BH, miss)
        spawn(mn_of(lp))
        a.label(miss)
        if right:
            miss = f"nb{bidx}"
            bidx += 1
            hit(a, L_MX, L_MY, RX, PAGE_Y + Y_EPAIR + i * 26, BW, BH, miss)
            spawn(mn_of(rp))
            a.label(miss)
    for i, (_n, p) in enumerate(enh_extra):
        miss = f"nb{bidx}"
        bidx += 1
        hit(a, L_MX, L_MY, LX + i * 208, PAGE_Y + Y_EEXTRA, BW, BH, miss)
        spawn(mn_of(p))
        a.label(miss)
    for i, (_n, p) in enumerate(orig_bosses):
        miss = f"nb{bidx}"
        bidx += 1
        hit(a, L_MX, L_MY, LX + (i % 2) * 208, PAGE_Y + Y_OPAIR + (i // 2) * 26, BW, BH, miss)
        spawn(mn_of(p))
        a.label(miss)
    for i, (_n, p) in enumerate(misc_mobs):
        miss = f"nb{bidx}"
        bidx += 1
        hit(a, L_MX, L_MY, 16 + (i % 3) * 140, PAGE_Y + Y_MROW + (i // 3) * 26, 132, BH, miss)
        spawn(mn_of(p))
        a.label(miss)

    for i, (xx, hh, mm) in enumerate(((16, 6, 0), (122, 12, 0), (228, 20, 0), (334, 0, 0))):
        miss = f"nt{i}"
        hit(a, L_MX, L_MY, xx, PAGE_Y + Y_TIME, 90, 24, miss)
        a.getlocal0()
        a.getproperty(world_mn)
        a.pushbyte(hh)
        a.pushbyte(mm)
        a.callpropvoid(set_time, 2)
        a.jump_to("click_done")
        a.label(miss)

    hit(a, L_MX, L_MY, OK_X, PAGE_Y + Y_HM, OK_W, 24, "c_fps")
    read_tf(s_tfH)
    dget(s_tfM)
    a.getproperty(text_mn)
    a.convert_i()
    a.setlocal(L_FMT)
    a.getlocal0()
    a.getproperty(world_mn)
    a.getlocal(L_TMP)
    a.getlocal(L_FMT)
    a.callpropvoid(set_time, 2)
    a.jump_to("click_done")
    a.label("c_fps")

    hit(a, L_MX, L_MY, OK_X, PAGE_Y + Y_MOBSP, OK_W, 24, "c_mobid")
    need_player("click_done")
    read_tf(s_tfMobId)
    a.getlocal(L_TMP)
    a.convert_i()
    a.setlocal(L_FMT)
    a.getlocal(L_FMT)
    a.pushbyte(1)
    a.iflt("click_done")
    a.getlocal(L_FMT)
    a.pushbyte(len(catalog_mobs))
    a.ifgt("click_done")
    clamp_qty(s_tfMobN)
    a.getlocal(L_TMP)
    a.setlocal(L_CNT)
    for i, (_n, path, _k) in enumerate(catalog_mobs):
        miss = f"mid{i}"
        a.getlocal(L_FMT)
        a.pushbyte(i + 1)
        a.ifne(miss)
        loop = uid("midl")
        a.label(loop)
        a.getlocal(L_CNT)
        a.pushbyte(1)
        a.iflt("click_done")
        spawn_one(mn_of(path))
        a.getlocal(L_CNT)
        a.decrement()
        a.convert_i()
        a.setlocal(L_CNT)
        a.jump_to(loop)
        a.label(miss)
    a.jump_to("click_done")
    a.label("c_mobid")
    a.jump_to("click_done")

    a.label("skip_cheats")
    a.getlocal(L_PAGE)
    a.pushbyte(1)
    a.ifne("skip_items")

    hit(a, L_MX, L_MY, 16, PAGE_Y + 8, 36, 24, "iprev")
    dget(s_itemPage)
    a.convert_i()
    a.setlocal(L_TMP)
    a.getlocal(L_TMP)
    a.pushbyte(0)
    a.ifle("do_fill")
    a.getlocal(L_TMP)
    a.decrement()
    a.convert_i()
    a.setlocal(L_TMP)
    a.getlocal(L_MENU)
    a.pushstring(s_itemPage)
    a.getlocal(L_TMP)
    a.setproperty_l(star_mn)
    a.jump_to("do_fill")
    a.label("iprev")
    hit(a, L_MX, L_MY, 60, PAGE_Y + 8, 36, 24, "inext")
    dget(s_itemPage)
    a.convert_i()
    a.pushbyte(1)
    a.add()
    a.convert_i()
    a.setlocal(L_TMP)
    a.getlocal(L_MENU)
    a.pushstring(s_itemPage)
    a.getlocal(L_TMP)
    a.setproperty_l(star_mn)
    a.jump_to("do_fill")
    a.label("inext")

    for i in range(40):
        miss = f"ic{i}"
        hit(a, L_MX, L_MY, IGX + (i % IC) * ICS, PAGE_Y + IGY + (i // IC) * ICS, 36, 36, miss)
        a.getlocal(L_MENU)
        a.pushstring(s_iid)
        a.pushbyte(i)
        a.convert_s()
        a.add()
        a.getproperty_l(star_mn)
        a.convert_i()
        a.setlocal(L_TMP)
        a.getlocal(L_TMP)
        a.pushbyte(0)
        a.ifle("click_done")
        a.getlocal(L_MENU)
        a.pushstring(s_holdId)
        a.getlocal(L_TMP)
        a.setproperty_l(star_mn)
        a.getlocal(L_MENU)
        a.pushstring(s_holdN)
        a.pushbyte(1)
        a.setproperty_l(star_mn)
        a.getlocal(L_MENU)
        a.pushstring(s_holdT)
        a.pushbyte(0)
        a.setproperty_l(star_mn)
        a.pushbyte(1)
        a.setlocal(L_CNT)
        a.jump_to("do_give")
        a.label(miss)
    a.jump_to("click_done")

    a.label("skip_items")
    a.getlocal(L_PAGE)
    a.pushbyte(2)
    a.ifne("skip_bosses")
    for i, (_n, path, _k) in enumerate(catalog_bosses):
        miss = f"cb{i}"
        bx, by = 16 + (i % 4) * 106, PAGE_Y + 36 + (i // 4) * 88
        hit(a, L_MX, L_MY, bx, by, 100, 84, miss)
        spawn_n(mn_of(path), s_tfBQty)
        a.label(miss)
    a.jump_to("click_done")

    a.label("skip_bosses")
    a.getlocal(L_PAGE)
    a.pushbyte(3)
    a.ifne("skip_mobs")
    for i, (_n, path, _k) in enumerate(catalog_mobs):
        miss = f"cm{i}"
        mx, my = 12 + (i % 6) * 70, PAGE_Y + 36 + (i // 6) * 86
        hit(a, L_MX, L_MY, mx, my, 68, 82, miss)
        spawn_n(mn_of(path), s_tfMQty)
        a.label(miss)
    a.jump_to("click_done")

    a.label("skip_mobs")
    a.getlocal(L_PAGE)
    a.pushbyte(4)
    a.ifne("click_done")
    hit(a, L_MX, L_MY, 16, PAGE_Y + Y_L0, 200, 28, "lg1")
    dget(s_tfLight)
    a.pushstring(s_light_h)
    a.setproperty(text_mn)
    a.jump_to("click_done")
    a.label("lg1")
    hit(a, L_MX, L_MY, 224, PAGE_Y + Y_L0, 200, 28, "lg2")
    dget(s_tfLight)
    a.pushstring(s_light1)
    a.setproperty(text_mn)
    a.jump_to("click_done")
    a.label("lg2")
    hit(a, L_MX, L_MY, OK_X, PAGE_Y + 208, OK_W, 24, "click_done")
    read_tf(s_tfFps)
    a.getlocal(L_TMP)
    a.pushbyte(10)
    a.iflt("fps_lo")
    a.getlocal(L_TMP)
    a.pushshort(240)
    a.ifgt("fps_hi")
    a.jump_to("fps_ok")
    a.label("fps_lo")
    a.pushbyte(10)
    a.setlocal(L_TMP)
    a.jump_to("fps_ok")
    a.label("fps_hi")
    a.pushshort(240)
    a.setlocal(L_TMP)
    a.label("fps_ok")
    a.getlocal0()
    a.getproperty(stage_mn)
    a.pushstring(s_frameRate)
    a.getlocal(L_TMP)
    a.convert_d()
    a.setproperty_l(star_mn)
    a.jump_to("click_done")

    a.label("do_give")
    need_player("give_done")
    a.getlex(item_cls)
    a.getproperty(items_mn)
    a.getlocal(L_TMP)
    a.getproperty_l(star_mn)
    a.coerce_a()
    a.setlocal(L_TGT)
    a.getlocal(L_TGT)
    a.pushnull()
    a.ifeq("give_done")
    a.getlocal(L_CNT)
    a.pushbyte(1)
    a.ifle("gv_add")
    a.getlocal(L_TGT)
    a.getproperty(max_stack_mn)
    a.convert_i()
    a.pushbyte(1)
    a.ifle("give_done")
    a.label("gv_add")
    a.getlocal0()
    a.getproperty(inventory_mn)
    a.getlocal(L_TGT)
    a.callproperty(count_mn, 1)
    a.convert_i()
    a.setlocal(L_FMT)
    a.getlocal(L_TGT)
    a.getproperty(max_stack_mn)
    a.convert_i()
    a.getlocal(L_FMT)
    a.subtract()
    a.convert_i()
    a.setlocal(L_FMT)
    a.getlocal(L_FMT)
    a.pushbyte(1)
    a.iflt("give_done")
    a.getlocal(L_CNT)
    a.getlocal(L_FMT)
    a.ifgt("gv_cap")
    a.jump_to("gv_do")
    a.label("gv_cap")
    a.getlocal(L_FMT)
    a.setlocal(L_CNT)
    a.label("gv_do")
    a.getlocal0()
    a.getproperty(inventory_mn)
    a.findpropstrict(stack_cls)
    a.getlocal(L_TGT)
    a.constructprop(stack_cls, 1)
    a.dup()
    a.getlocal(L_CNT)
    a.setproperty(count_mn)
    a.callpropvoid(add_mn, 1)
    a.pushbyte(0)
    a.setlocal(L_FMT)
    a.label("gv_hand")
    a.getlocal0()
    a.getproperty(inventory_mn)
    a.getproperty(inv_items_mn)
    a.getproperty(length_mn)
    a.convert_i()
    a.getlocal(L_FMT)
    a.ifle("give_done")
    a.getlocal0()
    a.getproperty(inventory_mn)
    a.getproperty(inv_items_mn)
    a.getlocal(L_FMT)
    a.getproperty_l(star_mn)
    a.coerce_a()
    a.setlocal(L_PAGE)
    a.getlocal(L_PAGE)
    a.pushnull()
    a.ifeq("gv_next")
    a.getlocal(L_PAGE)
    a.getproperty(item_prop_mn)
    a.getlocal(L_TGT)
    a.ifne("gv_next")
    a.getlocal0()
    a.getproperty(inventory_mn)
    a.getlocal(L_FMT)
    a.setproperty(hand_idx_mn)
    a.getlocal0()
    a.getproperty(controller_mn)
    a.getlocal(L_FMT)
    a.callpropvoid(set_sel_mn, 1)
    a.jump_to("give_done")
    a.label("gv_next")
    a.getlocal(L_FMT)
    a.pushbyte(1)
    a.add()
    a.convert_i()
    a.setlocal(L_FMT)
    a.jump_to("gv_hand")

    a.label("do_fill")
    dget(s_itemPage)
    a.convert_i()
    a.pushbyte(40)
    a.multiply()
    a.convert_i()
    a.setlocal(L_CNT)
    a.pushbyte(0)
    a.setlocal(L_FMT)
    a.pushbyte(1)
    a.setlocal(L_TMP)
    a.label("fi_loop")
    a.getlocal(L_FMT)
    a.pushbyte(40)
    a.ifge("fi_clear")
    a.getlocal(L_TMP)
    a.pushshort(4096)
    a.ifge("fi_clear")
    a.getlex(item_cls)
    a.getproperty(items_mn)
    a.getlocal(L_TMP)
    a.getproperty_l(star_mn)
    a.coerce_a()
    a.setlocal(L_TGT)
    a.getlocal(L_TGT)
    a.pushnull()
    a.ifeq("fi_next")
    a.getlex(images_cls)
    a.getproperty(items_mn)
    a.getlocal(L_TMP)
    a.getproperty_l(star_mn)
    a.coerce_a()
    a.setlocal(L_TGT)
    a.getlocal(L_TGT)
    a.pushnull()
    a.ifeq("fi_next")
    a.getlocal(L_CNT)
    a.pushbyte(0)
    a.ifle("fi_put")
    a.getlocal(L_CNT)
    a.decrement()
    a.convert_i()
    a.setlocal(L_CNT)
    a.jump_to("fi_next")
    a.label("fi_put")
    a.getlocal(L_MENU)
    a.pushstring(s_ib)
    a.getlocal(L_FMT)
    a.convert_s()
    a.add()
    a.getproperty_l(star_mn)
    a.coerce_a()
    a.setlocal(L_PAGE)
    a.getlocal(L_PAGE)
    a.pushnull()
    a.ifeq("fi_id")
    a.getlocal(L_PAGE)
    a.getlocal(L_TGT)
    a.setproperty(bitmapData_mn)
    a.getlocal(L_PAGE)
    a.pushshort(36)
    a.setproperty(width_mn)
    a.getlocal(L_PAGE)
    a.pushshort(36)
    a.setproperty(height_mn)
    a.getlocal(L_PAGE)
    a.pushtrue()
    a.setproperty(visible)
    a.label("fi_id")
    a.getlocal(L_MENU)
    a.pushstring(s_iid)
    a.getlocal(L_FMT)
    a.convert_s()
    a.add()
    a.getlocal(L_TMP)
    a.setproperty_l(star_mn)
    a.getlocal(L_FMT)
    a.pushbyte(1)
    a.add()
    a.convert_i()
    a.setlocal(L_FMT)
    a.label("fi_next")
    a.getlocal(L_TMP)
    a.pushbyte(1)
    a.add()
    a.convert_i()
    a.setlocal(L_TMP)
    a.jump_to("fi_loop")
    a.label("fi_clear")
    a.getlocal(L_FMT)
    a.pushbyte(40)
    a.ifge("click_done")
    a.getlocal(L_MENU)
    a.pushstring(s_ib)
    a.getlocal(L_FMT)
    a.convert_s()
    a.add()
    a.getproperty_l(star_mn)
    a.coerce_a()
    a.setlocal(L_PAGE)
    a.getlocal(L_PAGE)
    a.pushnull()
    a.ifeq("fi_c2")
    a.getlocal(L_PAGE)
    a.pushnull()
    a.setproperty(bitmapData_mn)
    a.getlocal(L_PAGE)
    a.pushfalse()
    a.setproperty(visible)
    a.label("fi_c2")
    a.getlocal(L_MENU)
    a.pushstring(s_iid)
    a.getlocal(L_FMT)
    a.convert_s()
    a.add()
    a.pushbyte(0)
    a.setproperty_l(star_mn)
    a.getlocal(L_FMT)
    a.pushbyte(1)
    a.add()
    a.convert_i()
    a.setlocal(L_FMT)
    a.jump_to("fi_clear")

    a.label("click_done")
    a.label("no_click")
    dset_local(s_mdWas, L_DOWN)

    a.getlocal(L_DOWN)
    a.convert_b()
    a.iffalse("hold_off")
    dget(s_holdN)
    a.convert_i()
    a.pushbyte(0)
    a.ifle("hold_done")
    dget(s_page)
    a.convert_i()
    a.pushbyte(1)
    a.ifne("hold_done")
    dget(s_holdT)
    a.convert_i()
    a.pushbyte(1)
    a.add()
    a.convert_i()
    a.setlocal(L_FMT)
    a.getlocal(L_MENU)
    a.pushstring(s_holdT)
    a.getlocal(L_FMT)
    a.setproperty_l(star_mn)
    a.getlocal(L_FMT)
    a.pushbyte(18)
    a.iflt("hold_done")
    a.getlocal(L_FMT)
    a.pushbyte(60)
    a.iflt("hold_n1")
    a.pushbyte(4)
    a.setlocal(L_CNT)
    a.jump_to("hold_go")
    a.label("hold_n1")
    a.pushbyte(1)
    a.setlocal(L_CNT)
    a.label("hold_go")
    dget(s_holdId)
    a.convert_i()
    a.setlocal(L_TMP)
    a.getlocal(L_TMP)
    a.pushbyte(0)
    a.ifle("hold_done")
    a.jump_to("do_give")
    a.label("hold_off")
    a.getlocal(L_MENU)
    a.pushstring(s_holdN)
    a.pushbyte(0)
    a.setproperty_l(star_mn)
    a.label("hold_done")
    a.label("give_done")
    dset_local(s_mdWas, L_DOWN)

    a.label("do_orig")
    print(f"    asm stack_max={a.max_used} code={len(a.code)}")
    prefix = a.finish()
    orig = orig_code
    if orig[:2] != bytes((0xD0, 0x30)):
        raise RuntimeError(f"SurvivalGame.preUpdate head {orig[:2].hex()}")
    return prefix + orig[2:]


def _abc_from_payload(payload: bytes):
    flags = struct.unpack_from("<I", payload, 0)[0]
    z = payload.find(b"\x00", 4)
    name = payload[4:z].decode("utf-8", "replace")
    return flags, name, payload[z + 1 :]


def patch_one(data: bytes) -> bytes:
    header_end = parse_rect(data, 8) + 4
    header = data[:header_end]
    tags = []
    f1 = f2 = None
    for start, code, long_len, payload in iter_tags(data):
        if code == 82:
            flags, name, raw = _abc_from_payload(payload)
            if name == "frame1":
                f1 = len(tags)
            elif name == "frame2":
                f2 = len(tags)
        tags.append((code, payload, long_len))
    if f1 is None or f2 is None:
        raise RuntimeError("frame1/frame2 DoABC missing")

    # frame1: Preloader.showError — reveal the real exception instead of "Error: #0"
    flags, name, abc_bytes = _abc_from_payload(tags[f1][1])
    abc = Abc(abc_bytes)
    orig_s, orig_ns, orig_mn = len(abc.strings), len(abc.namespaces), len(abc.multinames)
    mid = find_preloader_showerror(abc)
    print(f"    patching Preloader.showError method={mid} orig_len={len(abc.bodies[mid])}")
    new_code = build_show_error(abc)
    new_abc = apply_body_patch(
        abc_bytes, abc, orig_s, orig_ns, orig_mn, mid, new_code, max_stack=8, local_count=6
    )
    Abc(new_abc)  # parse check
    tags[f1] = (82, struct.pack("<I", flags) + name.encode() + b"\x00" + new_abc, True)

    # frame2: menu in Game.preUpdate
    flags, name, abc_bytes = _abc_from_payload(tags[f2][1])
    orig_abc = Abc(abc_bytes)
    ninst = len(orig_abc.instances)
    orig_s, orig_ns, orig_mn = len(orig_abc.strings), len(orig_abc.namespaces), len(orig_abc.multinames)
    mid = find_game_preupdate(orig_abc)
    print(f"    patching SurvivalGame.preUpdate method={mid} orig_len={len(orig_abc.bodies[mid])}")
    new_code = build_code(orig_abc, orig_abc.bodies[mid])
    new_abc = apply_body_patch(
        abc_bytes, orig_abc, orig_s, orig_ns, orig_mn, mid, new_code, max_stack=20, local_count=NLOCAL
    )
    # EXP1 (2026-09-12): patch_core_timestep ОТКЛЮЧЁН — главный подозреваемый
    # чёрного мира. Patch7 (3cc156d) работал без него; p9/p10 с ним дают
    # чёрный мир. Если в patch11 мир виден — timestep виноват, и 120 FPS
    # придётся делать иначе (без nop первого тика и prev=now вслепую).
    # new_abc = patch_core_timestep(new_abc)
    print("    verifying patched ABC…")
    assert_abc_ok(new_abc, ninst)
    tags[f2] = (82, struct.pack("<I", flags) + name.encode() + b"\x00" + new_abc, True)

    out = rebuild_swf(header, tags)
    # EXP1 (2026-09-12): header FPS остаётся 60, как в patch7
    # out = patch_fps_header(out, 120)
    return out


def main():
    print("load", SRC)
    base = load_swf(SRC)
    menu = patch_one(base)
    p1 = ROOT / f"orion_menu_patch{PATCH}.swf"
    write_swf(p1, menu, compressed=True)
    print("ok", p1.name)


if __name__ == "__main__":
    main()
