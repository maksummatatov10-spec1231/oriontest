#!/usr/bin/env python3
"""Build orion_menu_patchN.swf / orion_experemental_patchN.swf with in-SWF menu.

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
    BOSSES,
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
PATCH = 15

W, HEAD_H = 440, 38
COL_GOLD, COL_BG, COL_BTN = 0xE4C36A, 0x0B1020, 0x1A2438
COL_IN, COL_TXT, COL_RED = 0x0A0E18, 0xF3EAD6, 0x6B2A2A
COL_HEAD, COL_GIVE = 0x16120A, 0x3A5A2A

L_MENU, L_TMP, L_FMT = 4, 5, 6
L_MX, L_MY, L_DOWN, L_PL = 7, 8, 9, 10
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


def build_code(abc: Abc, orig_code: bytes, experimental: bool) -> bytes:
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
    star_mn = 0
    for i, (kind, nsi, namei, nset, extra) in enumerate(abc.multinames, start=1):
        if kind in (0x1B, 0x1C):
            star_mn = i
            break
    if not star_mn:
        raise RuntimeError("no MultinameL for Item.ITEMS[id]")

    boss_src = [
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
    for b in boss_src:
        ns, _, nm = b.rpartition("::")
        boss_mns.append(abc.find_qname(ns, nm))

    s_godOn = abc.intern_string("godOn")
    s_shiftWas = abc.intern_string("shiftWas")
    s_mdWas = abc.intern_string("mdWas")
    s_dragging = abc.intern_string("dragging")
    s_fpsSet = abc.intern_string("fpsSet")
    s_tfLvl = abc.intern_string("tfLvl")
    s_tfHp = abc.intern_string("tfHp")
    s_tfSpd = abc.intern_string("tfSpd")
    s_tfItem = abc.intern_string("tfItem")
    s_tfCnt = abc.intern_string("tfCnt")
    s_tfH = abc.intern_string("tfH")
    s_tfM = abc.intern_string("tfM")
    s_tfFps = abc.intern_string("tfFps")
    s_tfGod = abc.intern_string("tfGod")
    s_getChildByName = abc.intern_string("getChildByName")
    s_frameRate = abc.intern_string("frameRate")

    s_name = abc.intern_string("orionDev")
    s_sans = abc.intern_string("_sans")
    s_title = abc.intern_string("ORION  ·  Меню разработчика")
    s_hint = abc.intern_string("Insert / F7 — закрыть   ·   тащи за шапку")
    s_lvl = abc.intern_string("Уровень")
    s_hp = abc.intern_string("Здоровье")
    s_god_off = abc.intern_string("Режим бога: ВЫКЛ  (нажми)")
    s_god_on = abc.intern_string("Режим бога: ВКЛ  (нажми)")
    s_spd = abc.intern_string("Скорость")
    s_item = abc.intern_string("ID предмета")
    s_cnt = abc.intern_string("Кол-во")
    s_ok = abc.intern_string("OK")
    s_give = abc.intern_string("Выдать")
    s_boss = abc.intern_string("Боссы — клик, чтобы заспавнить рядом")
    s_h = abc.intern_string("ч")
    s_min = abc.intern_string("мин")
    s_morn = abc.intern_string("Утро")
    s_day = abc.intern_string("День")
    s_eve = abc.intern_string("Вечер")
    s_night = abc.intern_string("Ночь")
    s_set = abc.intern_string("Настройки Experimental")
    s_fpsl = abc.intern_string("FPS")
    s_gfx = abc.intern_string("Графика: тени / свет / частицы — скоро")
    s_help = abc.intern_string("34 кирка  36 жел.кирка  42 зол.меч  43 обс.меч  64 зелье  138 Громон")
    s_1 = abc.intern_string("1")
    s_100 = abc.intern_string("100")
    s_2 = abc.intern_string("2")
    s_12 = abc.intern_string("12")
    s_0 = abc.intern_string("0")
    s_120 = abc.intern_string("120")
    s_restrict = abc.intern_string("0-9")
    # --- tabs / new pages ---
    s_t0 = abc.intern_string("Быстрое")
    s_t1 = abc.intern_string("Предметы")
    s_t2 = abc.intern_string("Боссы")
    s_t3 = abc.intern_string("Мобы")
    s_t4 = abc.intern_string("Настройки")
    s_pg = abc.intern_string("pg")
    s_pg1 = abc.intern_string("pg1")
    s_pg2 = abc.intern_string("pg2")
    s_pg3 = abc.intern_string("pg3")
    s_pg4 = abc.intern_string("pg4")
    s_page = abc.intern_string("page")
    s_lightOn = abc.intern_string("lightOn")
    s_tfLight = abc.intern_string("tfLight")
    s_exp = abc.intern_string("Экспериментальные")
    s_gfxt = abc.intern_string("Настройки графики")
    s_light = abc.intern_string("Свет")
    s_light_off = abc.intern_string("Свет: обычный (нажми)")
    s_light_on = abc.intern_string("Свет: улучшенный (нажми)")
    s_bh = abc.intern_string("Orion Enhanced — обычные")
    s_bs = abc.intern_string("Orion Enhanced — призрачные")
    s_bo = abc.intern_string("Оригинальный Orion (нет в этой сборке)")
    s_qty = abc.intern_string("Кол-во")
    s_note_items = abc.intern_string("Сетка спрайтов предметов — в следующей сборке")
    s_note_mobs = abc.intern_string("Сетка спрайтов мобов — в следующей сборке")
    s_orig_1 = abc.intern_string("Gargoule  ->  Древний Страж")
    s_orig_2 = abc.intern_string("Huge Spider  ->  Король Москитон")
    s_orig_3 = abc.intern_string("Zartan  ->  Тиран")
    s_orig_4 = abc.intern_string("Fire Golem  ->  Огненный голем")
    boss_labels = [abc.intern_string(b["name"][:22]) for b in BOSSES]

    H = 640
    TAB_Y, TAB_H, TAB_W = 26, 28, 88
    DY = 30
    Y_LVL, Y_HP, Y_GOD, Y_SPD = 48 + DY, 80 + DY, 112 + DY, 144 + DY
    Y_ITEM, Y_HELP, Y_BOSS = 176 + DY, 208 + DY, 248 + DY
    Y_TIME, Y_HM = 428 + DY, 460 + DY
    Y_SET, Y_FPS, Y_GFX = 500 + DY, 532 + DY, 564 + DY
    OK_X, OK_W, IN_X, IN_W = 340, 84, 118, 210

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
    a.pushshort(0)
    a.setproperty(y_mn)
    a.getlocal(L_MENU)
    a.pushtrue()
    a.setproperty(visible)
    for prop in (s_godOn, s_shiftWas, s_mdWas, s_dragging, s_fpsSet):
        dset_false(prop)

    def fill(color, x, y, w, h, rnd=0, loc=None):
        loc = L_MENU if loc is None else loc
        a.getlocal(loc)
        a.getproperty(graphics)
        push_color(color)
        a.callpropvoid(beginFill, 1)
        a.getlocal(loc)
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
        a.getlocal(loc)
        a.getproperty(graphics)
        a.callpropvoid(endFill, 0)

    fill(COL_BG, 0, 0, W, H, 12)
    fill(COL_HEAD, 0, 0, W, HEAD_H, 0)
    fill(COL_BTN, OK_X, Y_LVL, OK_W, 24, 6)
    fill(COL_BTN, OK_X, Y_HP, OK_W, 24, 6)
    fill(COL_RED, 16, Y_GOD, W - 32, 26, 6)
    fill(COL_BTN, OK_X, Y_SPD, OK_W, 24, 6)
    fill(COL_GIVE, OK_X, Y_ITEM, OK_W, 24, 6)
    fill(COL_BTN, OK_X, Y_HM, OK_W, 24, 6)
    for xx in (16, 122, 228, 334):
        fill(COL_BTN, xx, Y_TIME, 90, 24, 6)
    for i in range(15):
        fill(COL_BTN, 16 + (i % 3) * 140, Y_BOSS + (i // 3) * 28, 132, 24, 5)
    if experimental:
        fill(COL_BTN, OK_X, Y_FPS, OK_W, 24, 6)

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

    def add_tf(text_idx, x, y, w, h, store=None, inp=False, def_text=None, loc=None):
        loc = L_MENU if loc is None else loc
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
        a.getlocal(loc)
        a.getlocal(L_TMP)
        a.callpropvoid(addChild, 1)
        if store:
            dset_local(store, L_TMP)

    add_tf(s_title, 12, 8, 420, 24)
    add_tf(s_hint, 12, H - 22, 420, 18)
    add_tf(s_lvl, 16, Y_LVL, 100, 22)
    add_tf(s_1, IN_X, Y_LVL, IN_W, 22, store=s_tfLvl, inp=True, def_text=s_1)
    add_tf(s_ok, OK_X + 28, Y_LVL + 2, 60, 20)
    add_tf(s_hp, 16, Y_HP, 100, 22)
    add_tf(s_100, IN_X, Y_HP, IN_W, 22, store=s_tfHp, inp=True, def_text=s_100)
    add_tf(s_ok, OK_X + 28, Y_HP + 2, 60, 20)
    add_tf(s_god_off, 28, Y_GOD + 3, 380, 22, store=s_tfGod)
    add_tf(s_spd, 16, Y_SPD, 100, 22)
    add_tf(s_2, IN_X, Y_SPD, IN_W, 22, store=s_tfSpd, inp=True, def_text=s_2)
    add_tf(s_ok, OK_X + 28, Y_SPD + 2, 60, 20)
    add_tf(s_item, 16, Y_ITEM, 100, 22)
    add_tf(s_1, IN_X, Y_ITEM, 90, 22, store=s_tfItem, inp=True, def_text=s_1)
    add_tf(s_cnt, 214, Y_ITEM, 60, 22)
    add_tf(s_1, 270, Y_ITEM, 60, 22, store=s_tfCnt, inp=True, def_text=s_1)
    add_tf(s_give, OK_X + 10, Y_ITEM + 2, 70, 20)
    add_tf(s_help, 16, Y_HELP, 408, 20)
    add_tf(s_boss, 16, Y_BOSS - 20, 400, 18)
    for i, lab in enumerate(boss_labels):
        add_tf(lab, 20 + (i % 3) * 140, Y_BOSS + (i // 3) * 28 + 3, 124, 18)
    add_tf(s_morn, 36, Y_TIME + 3, 70, 18)
    add_tf(s_day, 142, Y_TIME + 3, 70, 18)
    add_tf(s_eve, 244, Y_TIME + 3, 70, 18)
    add_tf(s_night, 348, Y_TIME + 3, 70, 18)
    add_tf(s_h, 16, Y_HM, 24, 22)
    add_tf(s_12, 40, Y_HM, 70, 22, store=s_tfH, inp=True, def_text=s_12)
    add_tf(s_min, 120, Y_HM, 40, 22)
    add_tf(s_0, 160, Y_HM, 70, 22, store=s_tfM, inp=True, def_text=s_0)
    add_tf(s_ok, OK_X + 28, Y_HM + 2, 60, 20)
    if experimental:
        add_tf(s_set, 16, Y_SET, 400, 20)
        add_tf(s_fpsl, 16, Y_FPS, 100, 22)
        add_tf(s_120, IN_X, Y_FPS, IN_W, 22, store=s_tfFps, inp=True, def_text=s_120)
        add_tf(s_ok, OK_X + 28, Y_FPS + 2, 60, 20)
        add_tf(s_gfx, 16, Y_GFX, 408, 20)

    # ================= TAB BAR =================
    fill(COL_HEAD, 0, TAB_Y, W, TAB_H, 0)
    for _i in range(5):
        fill(COL_BTN, 4 + _i * TAB_W, TAB_Y + 2, TAB_W - 8, TAB_H - 4, 5)

    # ================= PAGES =================
    def mkpage(sidx):
        a.findpropstrict(MC)
        a.constructprop(MC, 0)
        a.setlocal(L_TMP)
        a.getlocal(L_TMP)
        a.pushstring(sidx)
        a.setproperty(name_mn)
        a.getlocal(L_TMP)
        a.pushfalse()
        a.setproperty(visible)
        a.getlocal(L_MENU)
        a.getlocal(L_TMP)
        a.callpropvoid(addChild, 1)
        dset_local(sidx, L_TMP)

    def loadpage(sidx):
        dget(sidx)
        a.coerce_a()
        a.setlocal(L_TMP)

    ENH_N = [
        ("orion.worlds.entities.mobs.unique::UGargoyleEntity", "Древний Страж"),
        ("orion.worlds.entities.mobs.unique::UBigSpiderEntity", "Король Москитон"),
        ("orion.worlds.entities.mobs.unique::UZombieEntity", "Тиран"),
        ("orion.worlds.entities.mobs.unique::UfoEntity", "Император Финалиум"),
        ("orion.worlds.entities.mobs.unique::UGnomeEntity", "Свергнутый Король"),
        ("orion.worlds.entities.mobs.unique::UTransformerEntity", "Страж-машина"),
        ("orion.worlds.entities.mobs.unique::UStoneGolemEntity", "Огненный голем"),
    ]
    ENH_S = [
        ("orion.worlds.entities.mobs.unique::UBigShadowSpiderEntity", "Призрак Москитона"),
        ("orion.worlds.entities.mobs.unique::UShadowZombieEntity", "Призрак Тирана"),
        ("orion.worlds.entities.mobs.unique::UfoShadowEntity", "Призрак Императора"),
    ]
    # оригинальных классов в этой сборке нет — спавним Enhanced-аналог
    ORIG = [
        ("orion.worlds.entities.mobs.unique::UGargoyleEntity", "Gargoule > Древний Страж"),
        ("orion.worlds.entities.mobs.unique::UBigSpiderEntity", "Huge Spider > Москитон"),
        ("orion.worlds.entities.mobs.unique::UZombieEntity", "Zartan > Тиран"),
        ("orion.worlds.entities.mobs.unique::UStoneGolemEntity", "Fire Golem > Огн. голем"),
    ]

    def boss_mn(cls):
        ns, _, nm = cls.rpartition("::")
        return abc.find_qname(ns, nm)

    def spawn_n(mn, times):
        """Развёрнутый спавн N боссов — без циклов и без новых опкодов."""
        for _ in range(times):
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
            a.setlocal(L_TMP)
            a.getlocal0()
            a.getproperty(world_mn)
            a.getlocal(L_TMP)
            a.callpropvoid(add_creature, 1)

    # ---- pg1 Предметы ----
    mkpage(s_pg1)
    loadpage(s_pg1)
    fill(COL_BG, 0, 0, W, H, 12, loc=L_TMP)
    add_tf(s_note_items, 16, 10, 408, 22, loc=L_TMP)

    # ---- pg2 Боссы ----
    mkpage(s_pg2)
    loadpage(s_pg2)
    fill(COL_BG, 0, 0, W, H, 12, loc=L_TMP)
    add_tf(s_qty, 286, 8, 56, 20, loc=L_TMP)
    add_tf(s_1, 344, 6, 84, 22, store=s_tfCnt, inp=True, def_text=s_1, loc=L_TMP)
    add_tf(s_bh, 16, 34, 408, 20, loc=L_TMP)
    for _i, (_cls, _lab) in enumerate(ENH_N):
        _x = 16 + (_i % 2) * 212
        _y = 58 + (_i // 2) * 26
        fill(COL_BTN, _x, _y, 204, 24, 5, loc=L_TMP)
        add_tf(abc.intern_string(_lab), _x + 4, _y + 3, 196, 18, loc=L_TMP)
    add_tf(s_bs, 16, 176, 408, 20, loc=L_TMP)
    for _i, (_cls, _lab) in enumerate(ENH_S):
        _x = 16 + (_i % 2) * 212
        _y = 200 + (_i // 2) * 26
        fill(COL_BTN, _x, _y, 204, 24, 5, loc=L_TMP)
        add_tf(abc.intern_string(_lab), _x + 4, _y + 3, 196, 18, loc=L_TMP)
    add_tf(s_bo, 16, 284, 408, 20, loc=L_TMP)
    for _i, (_cls, _lab) in enumerate(ORIG):
        _y = 308 + _i * 26
        fill(COL_BTN, 16, _y, 408, 24, 5, loc=L_TMP)
        add_tf(abc.intern_string(_lab), 20, _y + 3, 400, 18, loc=L_TMP)

    # ---- pg3 Мобы ----
    mkpage(s_pg3)
    loadpage(s_pg3)
    fill(COL_BG, 0, 0, W, H, 12, loc=L_TMP)
    add_tf(s_note_mobs, 16, 10, 408, 22, loc=L_TMP)

    # ---- pg4 Настройки ----
    mkpage(s_pg4)
    loadpage(s_pg4)
    fill(COL_BG, 0, 0, W, H, 12, loc=L_TMP)
    add_tf(s_exp, 16, 10, 408, 20, loc=L_TMP)
    add_tf(s_fpsl, 16, 40, 90, 22, loc=L_TMP)
    add_tf(s_120, 116, 40, 190, 22, store=s_tfFps, inp=True, def_text=s_120, loc=L_TMP)
    fill(COL_BTN, 320, 40, 100, 24, 6, loc=L_TMP)
    add_tf(s_ok, 350, 42, 60, 20, loc=L_TMP)
    add_tf(s_gfxt, 16, 84, 408, 20, loc=L_TMP)
    fill(COL_BTN, 16, 110, 408, 28, 6, loc=L_TMP)
    add_tf(s_light_off, 24, 114, 396, 20, store=s_tfLight, loc=L_TMP)

    # ---- tab labels ----
    for _i, _s in enumerate((s_t0, s_t1, s_t2, s_t3, s_t4)):
        add_tf(_s, 4 + _i * TAB_W + 8, TAB_Y + 5, TAB_W - 16, 20)

    a.getlocal0()
    a.getproperty(stage_mn)
    a.getlocal(L_MENU)
    a.callpropvoid(addChild, 1)
    a.jump_to("after_init")
    a.label("inited")
    a.label("after_init")

    if experimental:
        dget(s_fpsSet)
        a.convert_b()
        a.iftrue("fps_done")
        a.getlocal0()
        a.getproperty(stage_mn)
        a.pushstring(s_frameRate)
        a.pushshort(120)
        a.convert_d()
        a.setproperty_l(star_mn)
        dset_true(s_fpsSet)
        a.label("fps_done")

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

    key_edge(45, s_shiftWas, "do_toggle", "no_sh")
    key_edge(118, s_shiftWas, "do_toggle", "no_f7")
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
    a.pushstring(s_shiftWas)
    a.getlocal0()
    a.getproperty(input_mn)
    a.pushbyte(45)
    a.callproperty(keyDown, 1)
    a.convert_b()
    a.dup()
    a.iftrue("shift_held")
    a.pop()
    a.getlocal0()
    a.getproperty(input_mn)
    a.pushbyte(118)
    a.callproperty(keyDown, 1)
    a.convert_b()
    a.label("shift_held")
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

    def need_player(miss):
        a.getlocal(L_PL)
        a.pushnull()
        a.ifeq(miss)

    def read_tf(sidx):
        dget(sidx)
        a.getproperty(text_mn)
        a.convert_i()
        a.setlocal(L_TMP)

    hit(a, L_MX, L_MY, OK_X, Y_LVL, OK_W, 24, "c_hp")
    need_player("click_done")
    read_tf(s_tfLvl)
    a.getlocal(L_PL)
    a.getlocal(L_TMP)
    a.setproperty(level_mn)
    a.jump_to("click_done")
    a.label("c_hp")

    hit(a, L_MX, L_MY, OK_X, Y_HP, OK_W, 24, "c_god")
    need_player("click_done")
    read_tf(s_tfHp)
    a.getlocal(L_PL)
    a.getlocal(L_TMP)
    a.setproperty(health_mn)
    a.jump_to("click_done")
    a.label("c_god")

    hit(a, L_MX, L_MY, 16, Y_GOD, W - 32, 26, "c_spd")
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

    hit(a, L_MX, L_MY, OK_X, Y_SPD, OK_W, 24, "c_item")
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

    hit(a, L_MX, L_MY, OK_X, Y_ITEM, OK_W, 24, "c_boss")
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

    for i, mn in enumerate(boss_mns):
        miss = f"nb{i}"
        hit(a, L_MX, L_MY, 16 + (i % 3) * 140, Y_BOSS + (i // 3) * 28, 132, 24, miss)
        need_player("click_done")
        if mn:
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
            a.setlocal(L_TMP)
            a.getlocal0()
            a.getproperty(world_mn)
            a.getlocal(L_TMP)
            a.callpropvoid(add_creature, 1)
        a.jump_to("click_done")
        a.label(miss)

    for i, (xx, hh, mm) in enumerate(((16, 6, 0), (122, 12, 0), (228, 20, 0), (334, 0, 0))):
        miss = f"nt{i}"
        hit(a, L_MX, L_MY, xx, Y_TIME, 90, 24, miss)
        a.getlocal0()
        a.getproperty(world_mn)
        a.pushbyte(hh)
        a.pushbyte(mm)
        a.callpropvoid(set_time, 2)
        a.jump_to("click_done")
        a.label(miss)

    hit(a, L_MX, L_MY, OK_X, Y_HM, OK_W, 24, "c_fps")
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

    if experimental:
        hit(a, L_MX, L_MY, OK_X, Y_FPS, OK_W, 24, "click_done")
        read_tf(s_tfFps)
        a.getlocal0()
        a.getproperty(stage_mn)
        a.pushstring(s_frameRate)
        a.getlocal(L_TMP)
        a.convert_d()
        a.setproperty_l(star_mn)

    # ================= TAB CLICKS + PAGE CONTENT =================
    def set_page_vis(active):
        for _idx, _sidx in enumerate((s_pg1, s_pg2, s_pg3, s_pg4), start=1):
            dget(_sidx)
            a.coerce_a()
            a.setlocal(L_TMP)
            a.getlocal(L_TMP)
            if _idx == active:
                a.pushtrue()
            else:
                a.pushfalse()
            a.setproperty(visible)

    for _i in range(5):
        _miss = f"tabm{_i}"
        hit(a, L_MX, L_MY, 4 + _i * TAB_W, TAB_Y, TAB_W - 8, TAB_H, _miss)
        a.getlocal(L_MENU)
        a.pushstring(s_page)
        a.pushbyte(_i)
        a.setproperty_l(star_mn)
        set_page_vis(_i)
        a.jump_to("click_done")
        a.label(_miss)

    # --- pg2 Боссы: кнопки (только когда открыта вкладка 2) ---
    dget(s_page)
    a.convert_i()
    a.pushbyte(2)
    a.ifne("no_boss_page")
    need_player("click_done")
    read_tf(s_tfCnt)
    a.getlocal(L_TMP)
    a.setlocal(13)
    # clamp 1..10 без новых опкодов: if 10 < cnt -> cnt = 10 ; if cnt < 1 -> cnt = 1
    a.pushbyte(10)
    a.getlocal(13)
    a.iflt("bq_ok1")
    a.pushbyte(10)
    a.setlocal(13)
    a.label("bq_ok1")
    a.getlocal(13)
    a.pushbyte(1)
    a.ifge("bq_ok2")
    a.pushbyte(1)
    a.setlocal(13)
    a.label("bq_ok2")

    def boss_button(x, y, w, cls):
        _mn = boss_mn(cls)
        _miss = f"bb{cls.split('::')[-1]}{x}{y}"
        hit(a, L_MX, L_MY, x, y, w, 24, _miss)
        if _mn:
            for _k in range(1, 11):
                a.pushbyte(_k)
                a.getlocal(13)
                a.iflt(f"sk_{_miss}_{_k}")
                spawn_n(_mn, 1)
                a.label(f"sk_{_miss}_{_k}")
        a.jump_to("click_done")
        a.label(_miss)

    for _i, (_cls, _lab) in enumerate(ENH_N):
        boss_button(16 + (_i % 2) * 212, 58 + (_i // 2) * 26, 204, _cls)
    for _i, (_cls, _lab) in enumerate(ENH_S):
        boss_button(16 + (_i % 2) * 212, 200 + (_i // 2) * 26, 204, _cls)
    for _i, (_cls, _lab) in enumerate(ORIG):
        boss_button(16, 308 + _i * 26, 408, _cls)
    a.label("no_boss_page")

    # --- pg4 Настройки: FPS + переключатель света ---
    dget(s_page)
    a.convert_i()
    a.pushbyte(4)
    a.ifne("no_set_page")
    hit(a, L_MX, L_MY, 320, 40, 100, 24, "set_light")
    read_tf(s_tfFps)
    a.getlocal(L_TMP)
    a.pushbyte(1)
    a.ifge("fps_apply")
    a.pushbyte(60)
    a.setlocal(L_TMP)
    a.label("fps_apply")
    a.getlocal0()
    a.getproperty(stage_mn)
    a.pushstring(s_frameRate)
    a.getlocal(L_TMP)
    a.convert_d()
    a.setproperty_l(star_mn)
    a.jump_to("click_done")
    a.label("set_light")
    hit(a, L_MX, L_MY, 16, 110, 408, 28, "click_done")
    dget(s_lightOn)
    a.convert_b()
    a.iftrue("light_to_false")
    a.pushtrue()
    a.jump_to("light_toggled")
    a.label("light_to_false")
    a.pushfalse()
    a.label("light_toggled")
    a.setlocal(L_TMP)
    a.getlocal(L_MENU)
    a.pushstring(s_lightOn)
    a.getlocal(L_TMP)
    a.setproperty_l(star_mn)
    dget(s_tfLight)
    a.coerce_a()
    a.setlocal(13)
    a.getlocal(13)
    a.getlocal(L_TMP)
    a.convert_b()
    a.iffalse("light_off_lbl")
    a.pushstring(s_light_on)
    a.jump_to("light_lbl_done")
    a.label("light_off_lbl")
    a.pushstring(s_light_off)
    a.label("light_lbl_done")
    a.setproperty(text_mn)
    a.label("no_set_page")

    a.label("click_done")
    a.label("no_click")
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


def patch_one(data: bytes, experimental: bool) -> bytes:
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
    new_code = build_code(orig_abc, orig_abc.bodies[mid], experimental)
    new_abc = apply_body_patch(
        abc_bytes, orig_abc, orig_s, orig_ns, orig_mn, mid, new_code, max_stack=16, local_count=NLOCAL
    )
    print("    verifying patched ABC…")
    assert_abc_ok(new_abc, ninst)
    tags[f2] = (82, struct.pack("<I", flags) + name.encode() + b"\x00" + new_abc, True)

    out = rebuild_swf(header, tags)
    if experimental:
        out = patch_fps_header(out, 120)
    return out


def main():
    print("load", SRC)
    base = load_swf(SRC)
    exp = patch_one(base, True)
    p2 = ROOT / f"orion_menu_patch{PATCH}.swf"
    write_swf(p2, exp, compressed=True)
    print("ok", p2.name)


if __name__ == "__main__":
    main()
