#!/usr/bin/env python3
"""Build orion_menu_patchN.swf / orion_experemental_patchN.swf with in-SWF menu.

Critical: AVM2 verifies EVERY method when the class loads. Unassigned locals
or stack mismatches in Game.update make AIR show only the preloader background.
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
PATCH = 1

W, HEAD_H = 440, 38
COL_GOLD, COL_BG, COL_BTN = 0xE4C36A, 0x0B1020, 0x1A2438
COL_IN, COL_TXT, COL_RED = 0x0A0E18, 0xF3EAD6, 0x6B2A2A

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
            # label only reached by jumps that weren't recorded? treat as 0
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
        # called after opcode-specific stack adjust
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

    def callproperty(self, mn, argc):
        self._use(-argc)  # obj+args -> result
        super().callproperty(mn, argc)

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

    def coerce_a(self):
        super().coerce_a()

    def add(self):
        self._use(-1)
        super().add()

    def divide(self):
        self._use(-1)
        super().divide()

    def not_(self):
        super().not_()

    def pushscope(self):
        self._use(-1)
        self.op(0x30)

    def finish(self) -> bytes:
        if self.stack != 0 and self.reachable:
            raise RuntimeError(f"finish stack={self.stack}")
        return super().finish()


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
    mid = find_game_update(a)
    if a.body_meta[mid]["code_len"] < 400:
        raise RuntimeError("update body too small")
    return a


def build_code(abc: Abc, orig_code: bytes, experimental: bool) -> A:
    def q(ns, name):
        return abc.find_qname(ns, name) or abc.intern_qname(ns, name)

    def n(name):
        return abc.find_name_any(name) or abc.intern_qname("", name)

    MC = q("flash.display", "MovieClip")
    TF = q("flash.text", "TextField")
    FMT = q("flash.text", "TextFormat")
    TFT = q("flash.text", "TextFieldType")
    INPUT = n("INPUT")
    graphics, beginFill, endFill = n("graphics"), n("beginFill"), n("endFill")
    drawRect, drawRoundRect = n("drawRect"), n("drawRoundRect")
    lineStyle = n("lineStyle") if abc.find_name_any("lineStyle") else abc.intern_qname("", "lineStyle")
    addChild = n("addChild")
    getChildByName = abc.intern_qname("", "getChildByName")
    startDrag, stopDrag = n("startDrag"), n("stopDrag")
    visible, name_mn, text_mn = n("visible"), n("name"), n("text")
    type_mn, border = n("type"), n("border")
    background = abc.intern_qname("", "background")
    bgColor, textColor = n("backgroundColor"), abc.intern_qname("", "textColor")
    selectable, mouseEnabled = n("selectable"), n("mouseEnabled")
    defaultTextFormat, embedFonts = n("defaultTextFormat"), n("embedFonts")
    font_mn, size_mn, color_mn = n("font"), n("size"), n("color")
    width_mn, height_mn, x_mn, y_mn = n("width"), n("height"), n("x"), n("y")
    mouseX, mouseY = n("mouseX"), n("mouseY")
    restrict, maxChars = n("restrict"), n("maxChars")
    stage_mn = n("stage")
    frameRate = abc.intern_qname("", "frameRate")
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

    p_god = abc.intern_qname("", "godOn")
    p_shift = abc.intern_qname("", "shiftWas")
    p_md = abc.intern_qname("", "mdWas")
    p_drag = abc.intern_qname("", "dragging")
    p_fps = abc.intern_qname("", "fpsSet")
    tfLvl = abc.intern_qname("", "tfLvl")
    tfHp = abc.intern_qname("", "tfHp")
    tfSpd = abc.intern_qname("", "tfSpd")
    tfItem = abc.intern_qname("", "tfItem")
    tfCnt = abc.intern_qname("", "tfCnt")
    tfH = abc.intern_qname("", "tfH")
    tfM = abc.intern_qname("", "tfM")
    tfFps = abc.intern_qname("", "tfFps")
    tfGod = abc.intern_qname("", "tfGod")

    s_name = abc.intern_string("orionDev")
    s_sans = abc.intern_string("_sans")
    s_title = abc.intern_string("ORION  ·  Меню разработчика")
    s_hint = abc.intern_string("Shift / F7 — закрыть   ·   тащи за шапку")
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
    boss_labels = [abc.intern_string(b["name"][:22]) for b in BOSSES]

    H = 640 if experimental else 548
    Y_LVL, Y_HP, Y_GOD, Y_SPD = 48, 80, 112, 144
    Y_ITEM, Y_HELP, Y_BOSS = 176, 208, 248
    Y_TIME, Y_HM = 428, 460
    Y_SET, Y_FPS, Y_GFX = 500, 532, 564
    OK_X, OK_W, IN_X, IN_W = 340, 84, 118, 210

    a = A()
    a.getlocal0()
    a.pushscope()

    # Definite assignment: verifier requires this for extra locals (2+ if orig used fewer).
    for i in range(2, NLOCAL):
        a.pushnull()
        a.setlocal(i)

    # stage?
    a.getlocal0()
    a.getproperty(stage_mn)
    a.pushnull()
    a.ifeq("do_orig")

    a.getlocal0()
    a.getproperty(stage_mn)
    a.pushstring(s_name)
    a.callproperty(getChildByName, 1)
    a.coerce_a()
    a.setlocal(L_MENU)
    a.getlocal(L_MENU)
    a.pushnull()
    a.ifne("inited")

    # ---- create menu ----
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
    for prop in (p_god, p_shift, p_md, p_drag, p_fps):
        a.getlocal(L_MENU)
        a.pushfalse()
        a.setproperty(prop)

    def fill(color, x, y, w, h, rnd=0):
        a.getlocal(L_MENU)
        a.getproperty(graphics)
        a.pushshort(color)
        a.callpropvoid(beginFill, 1)
        a.getlocal(L_MENU)
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
        a.getlocal(L_MENU)
        a.getproperty(graphics)
        a.callpropvoid(endFill, 0)

    a.getlocal(L_MENU)
    a.getproperty(graphics)
    a.pushbyte(2)
    a.pushshort(COL_GOLD)
    a.callpropvoid(lineStyle, 2)
    fill(COL_BG, 0, 0, W, H, 12)
    fill(0x16120A, 0, 0, W, HEAD_H, 0)
    fill(COL_BTN, OK_X, Y_LVL, OK_W, 24, 6)
    fill(COL_BTN, OK_X, Y_HP, OK_W, 24, 6)
    fill(COL_RED, 16, Y_GOD, W - 32, 26, 6)
    fill(COL_BTN, OK_X, Y_SPD, OK_W, 24, 6)
    fill(0x3A5A2A, OK_X, Y_ITEM, OK_W, 24, 6)
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
    a.pushshort(COL_TXT)
    a.setproperty(color_mn)

    def add_tf(text_idx, x, y, w, h, store=None, inp=False, def_text=None):
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
        a.getlocal(L_TMP)
        a.pushshort(COL_TXT)
        a.setproperty(textColor)
        if inp:
            a.getlocal(L_TMP)
            a.getlex(TFT)
            a.getproperty(INPUT)
            a.setproperty(type_mn)
            a.getlocal(L_TMP)
            a.pushtrue()
            a.setproperty(border)
            a.getlocal(L_TMP)
            a.pushtrue()
            a.setproperty(background)
            a.getlocal(L_TMP)
            a.pushshort(COL_IN)
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
        a.getlocal(L_MENU)
        a.getlocal(L_TMP)
        a.callpropvoid(addChild, 1)
        if store:
            a.getlocal(L_MENU)
            a.getlocal(L_TMP)
            a.setproperty(store)

    add_tf(s_title, 12, 8, 420, 24)
    add_tf(s_hint, 12, H - 22, 420, 18)
    add_tf(s_lvl, 16, Y_LVL, 100, 22)
    add_tf(s_1, IN_X, Y_LVL, IN_W, 22, store=tfLvl, inp=True, def_text=s_1)
    add_tf(s_ok, OK_X + 28, Y_LVL + 2, 60, 20)
    add_tf(s_hp, 16, Y_HP, 100, 22)
    add_tf(s_100, IN_X, Y_HP, IN_W, 22, store=tfHp, inp=True, def_text=s_100)
    add_tf(s_ok, OK_X + 28, Y_HP + 2, 60, 20)
    add_tf(s_god_off, 28, Y_GOD + 3, 380, 22, store=tfGod)
    add_tf(s_spd, 16, Y_SPD, 100, 22)
    add_tf(s_2, IN_X, Y_SPD, IN_W, 22, store=tfSpd, inp=True, def_text=s_2)
    add_tf(s_ok, OK_X + 28, Y_SPD + 2, 60, 20)
    add_tf(s_item, 16, Y_ITEM, 100, 22)
    add_tf(s_1, IN_X, Y_ITEM, 90, 22, store=tfItem, inp=True, def_text=s_1)
    add_tf(s_cnt, 214, Y_ITEM, 60, 22)
    add_tf(s_1, 270, Y_ITEM, 60, 22, store=tfCnt, inp=True, def_text=s_1)
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
    add_tf(s_12, 40, Y_HM, 70, 22, store=tfH, inp=True, def_text=s_12)
    add_tf(s_min, 120, Y_HM, 40, 22)
    add_tf(s_0, 160, Y_HM, 70, 22, store=tfM, inp=True, def_text=s_0)
    add_tf(s_ok, OK_X + 28, Y_HM + 2, 60, 20)
    if experimental:
        add_tf(s_set, 16, Y_SET, 400, 20)
        add_tf(s_fpsl, 16, Y_FPS, 100, 22)
        add_tf(s_120, IN_X, Y_FPS, IN_W, 22, store=tfFps, inp=True, def_text=s_120)
        add_tf(s_ok, OK_X + 28, Y_FPS + 2, 60, 20)
        add_tf(s_gfx, 16, Y_GFX, 408, 20)

    a.getlocal0()
    a.getproperty(stage_mn)
    a.getlocal(L_MENU)
    a.callpropvoid(addChild, 1)
    a.jump_to("after_init")
    a.label("inited")
    a.label("after_init")

    if experimental:
        a.getlocal(L_MENU)
        a.getproperty(p_fps)
        a.convert_b()
        a.iftrue("fps_done")
        a.getlocal0()
        a.getproperty(stage_mn)
        a.pushshort(120)
        a.convert_d()
        a.setproperty(frameRate)
        a.getlocal(L_MENU)
        a.pushtrue()
        a.setproperty(p_fps)
        a.label("fps_done")

    # always assign L_PL
    a.getlocal0()
    a.getproperty(player_mn)
    a.coerce_a()
    a.setlocal(L_PL)

    a.getlocal(L_PL)
    a.pushnull()
    a.ifeq("ui_keys")
    a.getlocal(L_MENU)
    a.getproperty(p_god)
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

    def key_edge(code, was_prop, hit_lab, skip_lab):
        a.getlocal0()
        a.getproperty(input_mn)
        a.pushbyte(code)
        a.callproperty(keyDown, 1)
        a.convert_b()
        a.setlocal(L_DOWN)
        a.getlocal(L_DOWN)
        a.convert_b()
        a.dup()
        a.iffalse(skip_lab + "_e")
        a.pop()
        a.getlocal(L_MENU)
        a.getproperty(was_prop)
        a.convert_b()
        a.not_()
        a.label(skip_lab + "_e")
        a.convert_b()
        a.iffalse(skip_lab)
        a.jump_to(hit_lab)
        a.label(skip_lab)

    key_edge(16, p_shift, "do_toggle", "no_sh")
    key_edge(118, p_shift, "do_toggle", "no_f7")  # F7
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
    a.getlocal0()
    a.getproperty(input_mn)
    a.pushbyte(16)
    a.callproperty(keyDown, 1)
    a.convert_b()
    a.setproperty(p_shift)

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

    # drag
    a.getlocal(L_MENU)
    a.getproperty(p_drag)
    a.convert_b()
    a.iffalse("not_dragging")
    a.getlocal(L_DOWN)
    a.convert_b()
    a.iftrue("keep_drag")
    a.getlocal(L_MENU)
    a.callpropvoid(stopDrag, 0)
    a.getlocal(L_MENU)
    a.pushfalse()
    a.setproperty(p_drag)
    a.label("keep_drag")
    a.jump_to("after_drag")
    a.label("not_dragging")
    a.label("after_drag")

    a.getlocal(L_DOWN)
    a.convert_b()
    a.dup()
    a.iffalse("no_just")
    a.pop()
    a.getlocal(L_MENU)
    a.getproperty(p_md)
    a.convert_b()
    a.not_()
    a.label("no_just")
    a.convert_b()
    a.iffalse("no_click")

    hit(a, L_MX, L_MY, 0, 0, W, HEAD_H, "not_head")
    a.getlocal(L_MENU)
    a.callpropvoid(startDrag, 0)
    a.getlocal(L_MENU)
    a.pushtrue()
    a.setproperty(p_drag)
    a.jump_to("click_done")
    a.label("not_head")

    def need_player(miss):
        a.getlocal(L_PL)
        a.pushnull()
        a.ifeq(miss)

    def read_tf(store):
        a.getlocal(L_MENU)
        a.getproperty(store)
        a.getproperty(text_mn)
        a.convert_i()
        a.setlocal(L_TMP)

    hit(a, L_MX, L_MY, OK_X, Y_LVL, OK_W, 24, "c_hp")
    need_player("click_done")
    read_tf(tfLvl)
    a.getlocal(L_PL)
    a.getlocal(L_TMP)
    a.setproperty(level_mn)
    a.jump_to("click_done")
    a.label("c_hp")

    hit(a, L_MX, L_MY, OK_X, Y_HP, OK_W, 24, "c_god")
    need_player("click_done")
    read_tf(tfHp)
    a.getlocal(L_PL)
    a.getlocal(L_TMP)
    a.setproperty(health_mn)
    a.jump_to("click_done")
    a.label("c_god")

    hit(a, L_MX, L_MY, 16, Y_GOD, W - 32, 26, "c_spd")
    a.getlocal(L_MENU)
    a.getlocal(L_MENU)
    a.getproperty(p_god)
    a.convert_b()
    a.not_()
    a.setproperty(p_god)
    a.getlocal(L_MENU)
    a.getproperty(p_god)
    a.convert_b()
    a.iffalse("god_off")
    a.getlocal(L_MENU)
    a.getproperty(tfGod)
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
    a.getlocal(L_MENU)
    a.getproperty(tfGod)
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
    a.getlocal(L_MENU)
    a.getproperty(tfSpd)
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
    a.getlocal(L_MENU)
    a.getproperty(tfItem)
    a.getproperty(text_mn)
    a.convert_i()
    a.getproperty_l(star_mn)
    a.coerce_a()
    a.setlocal(L_FMT)
    a.getlocal(L_FMT)
    a.pushnull()
    a.ifeq("click_done")
    read_tf(tfCnt)
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
    read_tf(tfH)
    a.getlocal(L_MENU)
    a.getproperty(tfM)
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
        read_tf(tfFps)
        a.getlocal0()
        a.getproperty(stage_mn)
        a.getlocal(L_TMP)
        a.convert_d()
        a.setproperty(frameRate)

    a.label("click_done")
    a.label("no_click")
    a.getlocal(L_MENU)
    a.getlocal(L_DOWN)
    a.setproperty(p_md)

    a.label("do_orig")
    orig = orig_code[2:] if orig_code[:2] == bytes((0xD0, 0x30)) else orig_code
    print(f"    asm stack_max={a.max_used} code={len(a.code)}")
    return a.finish() + orig


def patch_one(data: bytes, experimental: bool) -> bytes:
    header_end = parse_rect(data, 8) + 4
    header = data[:header_end]
    tags = []
    abc_index = abc_name = abc_flags = abc_bytes = None
    for start, code, long_len, payload in iter_tags(data):
        if code == 82:
            flags = struct.unpack_from("<I", payload, 0)[0]
            z = payload.find(b"\x00", 4)
            name = payload[4:z].decode("utf-8", "replace")
            raw = payload[z + 1 :]
            if name == "frame2" or abc_bytes is None or len(raw) > len(abc_bytes or b""):
                abc_index, abc_name, abc_flags, abc_bytes = len(tags), name, flags, raw
        tags.append((code, payload))
    orig_abc = Abc(abc_bytes)
    ninst = len(orig_abc.instances)
    orig_s, orig_ns, orig_mn = len(orig_abc.strings), len(orig_abc.namespaces), len(orig_abc.multinames)
    mid = find_game_update(orig_abc)
    new_code = build_code(orig_abc, orig_abc.bodies[mid], experimental)
    new_abc = apply_body_patch(
        abc_bytes, orig_abc, orig_s, orig_ns, orig_mn, mid, new_code, max_stack=16, local_count=NLOCAL
    )
    print("    verifying patched ABC…")
    assert_abc_ok(new_abc, ninst)
    tags[abc_index] = (82, struct.pack("<I", abc_flags) + abc_name.encode() + b"\x00" + new_abc)
    out = rebuild_swf(header, tags)
    if experimental:
        out = patch_fps_header(out, 120)
    return out


def main():
    print("load", SRC)
    base = load_swf(SRC)
    menu = patch_one(base, False)
    p1 = ROOT / f"orion_menu_patch{PATCH}.swf"
    write_swf(p1, menu, compressed=True)
    exp = patch_one(base, True)
    p2 = ROOT / f"orion_experemental_patch{PATCH}.swf"
    write_swf(p2, exp, compressed=True)
    print("ok", p1.name, p2.name)


if __name__ == "__main__":
    main()
