#!/usr/bin/env python3
"""Build orion_menu.swf and orion_experemental.swf with in-game Flash UI."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from patch_orion import (
    Abc,
    Asm,
    BOSSES,
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

# layout
W, HEAD_H = 440, 38
COL_GOLD = 0xE4C36A
COL_BG = 0x0B1020
COL_BTN = 0x1A2438
COL_IN = 0x0A0E18
COL_TXT = 0xF3EAD6
COL_RED = 0x6B2A2A
COL_GOD = 0x2A4A32


def patch_fps_header(data: bytes, fps: int) -> bytes:
    pos = parse_rect(data, 8)
    out = bytearray(data)
    out[pos : pos + 2] = struct.pack("<H", int(fps * 256) & 0xFFFF)
    return bytes(out)


def apply_body_patch(abc_bytes: bytes, abc: Abc, orig_s, orig_ns, orig_mn, mid, new_code, max_stack=12, local_count=14):
    meta = dict(abc.body_meta[mid])
    prefix = abc.rebuild_prefix(orig_s, orig_ns, orig_mn)
    tail = abc_bytes[abc.tail_off :]
    shift = len(prefix) - abc.tail_off
    new_abc = bytearray(prefix + tail)

    def patch_u30_field(orig_off, new_val, old_val):
        off = orig_off + shift
        _, after = u30(bytes(new_abc), off)
        old_len = after - off
        new_enc = enc_u30(new_val)
        new_abc[off : off + old_len] = new_enc
        return len(new_enc) - old_len

    d1 = patch_u30_field(meta["max_stack_off"], max(meta["max_stack"], max_stack), meta["max_stack"])
    meta["local_count_off"] += d1
    meta["code_len_off"] += d1
    meta["code_off"] += d1
    d2 = patch_u30_field(meta["local_count_off"], max(meta["local_count"], local_count), meta["local_count"])
    meta["code_len_off"] += d2
    meta["code_off"] += d2
    d3 = patch_u30_field(meta["code_len_off"], len(new_code), meta["code_len"])
    meta["code_off"] += d3
    code_off = meta["code_off"] + shift
    new_abc[code_off : code_off + meta["code_len"]] = new_code
    return bytes(new_abc)


class A(Asm):
    def iflt(self, lab):
        self.jump(0x15, lab)

    def ifge(self, lab):
        self.jump(0x18, lab)

    def ifgt(self, lab):
        self.jump(0x17, lab)

    def ifle(self, lab):
        self.jump(0x16, lab)

    def pushscope(self):
        self.op(0x30)

    def convert_s(self):
        self.op(0x70)

    def increment(self):
        self.op(0x91)

    def decrement(self):
        self.op(0x93)

    def subtract(self):
        self.op(0xA1)

    def bitand(self):
        self.op(0xA8)

    def negate(self):
        self.op(0x90)


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


def build_code(abc: Abc, orig_code: bytes, experimental: bool) -> bytes:
    def q(ns, name):
        return abc.find_qname(ns, name) or abc.intern_qname(ns, name)

    def n(name):
        return abc.find_name_any(name) or abc.intern_qname("", name)

    MC = q("flash.display", "MovieClip")
    TF = q("flash.text", "TextField")
    FMT = q("flash.text", "TextFormat")
    TFT = q("flash.text", "TextFieldType")
    INPUT = n("INPUT")
    graphics = n("graphics")
    beginFill = n("beginFill")
    endFill = n("endFill")
    drawRect = n("drawRect")
    drawRoundRect = n("drawRoundRect")
    lineStyle = n("lineStyle") if abc.find_name_any("lineStyle") else abc.intern_qname("", "lineStyle")
    addChild = n("addChild")
    getChildByName = abc.intern_qname("", "getChildByName")
    startDrag = n("startDrag")
    stopDrag = n("stopDrag")
    visible = n("visible")
    name_mn = n("name")
    text_mn = n("text")
    type_mn = n("type")
    border = n("border")
    background = abc.intern_qname("", "background")
    bgColor = n("backgroundColor")
    textColor = abc.intern_qname("", "textColor")
    selectable = n("selectable")
    mouseEnabled = n("mouseEnabled")
    mouseChildren = n("mouseChildren")
    defaultTextFormat = n("defaultTextFormat")
    embedFonts = n("embedFonts")
    font_mn = n("font")
    size_mn = n("size")
    color_mn = n("color")
    bold_mn = n("bold")
    width_mn = n("width")
    height_mn = n("height")
    x_mn = n("x")
    y_mn = n("y")
    mouseX = n("mouseX")
    mouseY = n("mouseY")
    multiline = n("multiline")
    wordWrap = n("wordWrap")
    restrict = n("restrict")
    maxChars = n("maxChars")
    stage_mn = n("stage")
    frameRate = abc.intern_qname("", "frameRate")
    player_mn = n("player")
    world_mn = n("world")
    inventory_mn = n("inventory")
    health_mn = n("health")
    max_health_mn = abc.find_qname("orion.worlds.entities", "maxHealth") or n("maxHealth")
    immortal_mn = n("immortal")
    level_mn = n("level")
    speed_mn = n("moveSpeed")
    items_mn = n("ITEMS")
    item_cls = q("orion.worlds", "Item")
    stack_cls = q("orion.worlds", "ItemStack")
    add_mn = n("add")
    count_mn = n("count")
    set_time = n("setGameTime")
    add_creature = n("addCreature")
    pos_mn = n("position")
    input_mn = n("input")
    keyDown = n("keyDown")
    mouseDown = n("mouseDown")
    star_mn = 0
    for i, (kind, nsi, namei, nset, extra) in enumerate(abc.multinames, start=1):
        if kind in (0x1B, 0x1C):
            star_mn = i
            break

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
        ns, _, nm = b.rpartition("::")
        boss_mns.append(abc.find_qname(ns, nm))

    # dynamic props on MovieClip
    p_god = abc.intern_qname("", "godOn")
    p_shift = abc.intern_qname("", "shiftWas")
    p_md = abc.intern_qname("", "mdWas")
    p_drag = abc.intern_qname("", "dragging")
    p_initedFps = abc.intern_qname("", "fpsSet")
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
    s_hint = abc.intern_string("Right Shift — закрыть   ·   тащи за шапку")
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
    s_time = abc.intern_string("Время")
    s_h = abc.intern_string("ч")
    s_min = abc.intern_string("мин")
    s_morn = abc.intern_string("Утро")
    s_day = abc.intern_string("День")
    s_eve = abc.intern_string("Вечер")
    s_night = abc.intern_string("Ночь")
    s_set = abc.intern_string("Настройки Experimental")
    s_fps = abc.intern_string("FPS")
    s_gfx = abc.intern_string("Графика: тени / свет / частицы — скоро")
    s_help = abc.intern_string(
        "34 кирка  36 жел.кирка  42 зол.меч  43 обс.меч\n"
        "64 зелье  138 Громон  50 шлем  20 лопата"
    )
    s_1 = abc.intern_string("1")
    s_100 = abc.intern_string("100")
    s_20 = abc.intern_string("2")
    s_12 = abc.intern_string("12")
    s_0 = abc.intern_string("0")
    s_120 = abc.intern_string("120")
    s_restrict = abc.intern_string("0-9")
    boss_labels = []
    for b in BOSSES:
        boss_labels.append(abc.intern_string(b["name"][:22]))

    H = 640 if experimental else 548
    # button rows
    Y_LVL, Y_HP, Y_GOD, Y_SPD = 48, 80, 112, 144
    Y_ITEM, Y_HELP, Y_BOSS = 176, 208, 248
    Y_TIME, Y_HM = 428, 460
    Y_SET, Y_FPS, Y_GFX = 500, 532, 564
    OK_X, OK_W, ROW_H = 340, 84, 28
    IN_X, IN_W = 118, 210

    L_PLAYER, L_MENU, L_TMP, L_FMT = 4, 5, 6, 7
    L_MX, L_MY, L_DOWN = 8, 9, 10

    a = A()
    a.getlocal0()
    a.pushscope()

    # stage null?
    a.getlocal0()
    a.getproperty(stage_mn)
    a.pushnull()
    a.ifeq("do_orig")

    # getChildByName
    a.getlocal0()
    a.getproperty(stage_mn)
    a.pushstring(s_name)
    a.callproperty(getChildByName, 1)
    a.coerce_a()
    a.setlocal(L_MENU)
    a.getlocal(L_MENU)
    a.pushnull()
    a.ifne("inited")

    # ---------- CREATE MENU ----------
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
    a.getlocal(L_MENU)
    a.pushfalse()
    a.setproperty(p_god)
    a.getlocal(L_MENU)
    a.pushfalse()
    a.setproperty(p_shift)
    a.getlocal(L_MENU)
    a.pushfalse()
    a.setproperty(p_md)
    a.getlocal(L_MENU)
    a.pushfalse()
    a.setproperty(p_drag)
    a.getlocal(L_MENU)
    a.pushfalse()
    a.setproperty(p_initedFps)

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

    # panel
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
    # time presets
    for i, xx in enumerate((16, 122, 228, 334)):
        fill(COL_BTN, xx, Y_TIME, 90, 24, 6)
    # boss buttons
    for i in range(15):
        col, row = i % 3, i // 3
        fill(COL_BTN, 16 + col * 140, Y_BOSS + row * 28, 132, 24, 5)
    if experimental:
        fill(COL_BTN, OK_X, Y_FPS, OK_W, 24, 6)

    # format
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

    def add_tf(text_idx, x, y, w, h, store=None, inp=False, def_text=None, mouse=False, size=13):
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
    add_tf(s_20, IN_X, Y_SPD, IN_W, 22, store=tfSpd, inp=True, def_text=s_20)
    add_tf(s_ok, OK_X + 28, Y_SPD + 2, 60, 20)
    add_tf(s_item, 16, Y_ITEM, 100, 22)
    add_tf(s_1, IN_X, Y_ITEM, 90, 22, store=tfItem, inp=True, def_text=s_34 if False else s_1)
    add_tf(s_cnt, 214, Y_ITEM, 60, 22)
    add_tf(s_1, 270, Y_ITEM, 60, 22, store=tfCnt, inp=True, def_text=s_1)
    add_tf(s_give, OK_X + 10, Y_ITEM + 2, 70, 20)
    add_tf(s_help, 16, Y_HELP, 408, 36)
    add_tf(s_boss, 16, Y_BOSS - 20, 400, 18)
    for i, lab in enumerate(boss_labels):
        col, row = i % 3, i // 3
        add_tf(lab, 20 + col * 140, Y_BOSS + row * 28 + 3, 124, 18)
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
        add_tf(s_fps, 16, Y_FPS, 100, 22)
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

    # experimental default fps once
    if experimental:
        a.getlocal(L_MENU)
        a.getproperty(p_initedFps)
        a.convert_b()
        a.iftrue("fps_done")
        a.getlocal0()
        a.getproperty(stage_mn)
        a.pushshort(120)
        a.convert_d()
        a.setproperty(frameRate)
        a.getlocal(L_MENU)
        a.pushtrue()
        a.setproperty(p_initedFps)
        a.label("fps_done")

    # player
    a.getlocal0()
    a.getproperty(player_mn)
    a.pushnull()
    a.ifeq("ui_keys")
    a.getlocal0()
    a.getproperty(player_mn)
    a.setlocal(L_PLAYER)

    # god tick
    a.getlocal(L_MENU)
    a.getproperty(p_god)
    a.convert_b()
    a.iffalse("no_god_tick")
    a.getlocal(L_PLAYER)
    a.pushtrue()
    a.setproperty(immortal_mn)
    a.getlocal(L_PLAYER)
    a.getlocal(L_PLAYER)
    a.getproperty(max_health_mn)
    a.setproperty(health_mn)
    a.label("no_god_tick")

    a.label("ui_keys")
    # SHIFT toggle (16) edge
    a.getlocal0()
    a.getproperty(input_mn)
    a.pushbyte(16)
    a.callproperty(keyDown, 1)
    a.convert_b()
    a.setlocal(L_DOWN)  # reuse as shiftNow
    a.getlocal(L_DOWN)
    a.convert_b()
    a.dup()
    a.iffalse("no_sh_edge")
    a.pop()
    a.getlocal(L_MENU)
    a.getproperty(p_shift)
    a.convert_b()
    a.not_()
    a.label("no_sh_edge")
    a.convert_b()
    a.iffalse("after_shift")
    # toggle visible
    a.getlocal(L_MENU)
    a.getlocal(L_MENU)
    a.getproperty(visible)
    a.convert_b()
    a.not_()
    a.setproperty(visible)
    # raise
    a.getlocal0()
    a.getproperty(stage_mn)
    a.getlocal(L_MENU)
    a.callpropvoid(addChild, 1)
    a.label("after_shift")
    a.getlocal(L_MENU)
    a.getlocal(L_DOWN)
    a.setproperty(p_shift)

    # if not visible, skip clicks
    a.getlocal(L_MENU)
    a.getproperty(visible)
    a.convert_b()
    a.iffalse("do_orig")

    # mouse
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

    # drag header
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

    # just click
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

    # header drag start
    hit(a, L_MX, L_MY, 0, 0, W, HEAD_H, "not_head")
    a.getlocal(L_MENU)
    a.callpropvoid(startDrag, 0)
    a.getlocal(L_MENU)
    a.pushtrue()
    a.setproperty(p_drag)
    a.jump_to("click_done")
    a.label("not_head")

    def read_tf(store_mn, dest_local):
        a.getlocal(L_MENU)
        a.getproperty(store_mn)
        a.getproperty(text_mn)
        a.convert_i()
        a.setlocal(dest_local)

    # OK level
    hit(a, L_MX, L_MY, OK_X, Y_LVL, OK_W, 24, "c_hp")
    a.getlocal0()
    a.getproperty(player_mn)
    a.pushnull()
    a.ifeq("click_done")
    read_tf(tfLvl, L_TMP)
    a.getlocal(L_PLAYER)
    a.getlocal(L_TMP)
    a.setproperty(level_mn)
    a.jump_to("click_done")
    a.label("c_hp")

    hit(a, L_MX, L_MY, OK_X, Y_HP, OK_W, 24, "c_god")
    a.getlocal0()
    a.getproperty(player_mn)
    a.pushnull()
    a.ifeq("click_done")
    read_tf(tfHp, L_TMP)
    a.getlocal(L_PLAYER)
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
    a.getlocal0()
    a.getproperty(player_mn)
    a.pushnull()
    a.ifeq("click_done")
    a.getlocal(L_PLAYER)
    a.pushtrue()
    a.setproperty(immortal_mn)
    a.jump_to("click_done")
    a.label("god_off")
    a.getlocal(L_MENU)
    a.getproperty(tfGod)
    a.pushstring(s_god_off)
    a.setproperty(text_mn)
    a.getlocal0()
    a.getproperty(player_mn)
    a.pushnull()
    a.ifeq("click_done")
    a.getlocal(L_PLAYER)
    a.pushfalse()
    a.setproperty(immortal_mn)
    a.jump_to("click_done")
    a.label("c_spd")

    hit(a, L_MX, L_MY, OK_X, Y_SPD, OK_W, 24, "c_item")
    a.getlocal0()
    a.getproperty(player_mn)
    a.pushnull()
    a.ifeq("click_done")
    a.getlocal(L_MENU)
    a.getproperty(tfSpd)
    a.getproperty(text_mn)
    a.convert_d()
    a.setlocal(L_TMP)
    a.getlocal(L_PLAYER)
    a.getlocal(L_TMP)
    a.setproperty(speed_mn)
    a.jump_to("click_done")
    a.label("c_item")

    hit(a, L_MX, L_MY, OK_X, Y_ITEM, OK_W, 24, "c_boss")
    a.getlocal0()
    a.getproperty(player_mn)
    a.pushnull()
    a.ifeq("click_done")
    read_tf(tfItem, L_TMP)
    a.getlex(item_cls)
    a.getproperty(items_mn)
    a.getlocal(L_TMP)
    a.getproperty(star_mn)
    a.coerce_a()
    a.setlocal(L_FMT)  # reuse as item ptr
    a.getlocal(L_FMT)
    a.pushnull()
    a.ifeq("click_done")
    read_tf(tfCnt, L_TMP)
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

    # bosses
    for i, mn in enumerate(boss_mns):
        col, row = i % 3, i // 3
        miss = f"nb{i}"
        hit(a, L_MX, L_MY, 16 + col * 140, Y_BOSS + row * 28, 132, 24, miss)
        a.getlocal0()
        a.getproperty(player_mn)
        a.pushnull()
        a.ifeq("click_done")
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
            a.getlocal0()
            a.getproperty(world_mn)
            a.getlocal(L_TMP)
            a.callpropvoid(add_creature, 1)
        a.jump_to("click_done")
        a.label(miss)

    # time presets
    presets = [(16, 6, 0), (122, 12, 0), (228, 20, 0), (334, 0, 0)]
    for i, (xx, hh, mm) in enumerate(presets):
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
    read_tf(tfH, L_TMP)
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
        read_tf(tfFps, L_TMP)
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
    orig = orig_code
    if orig[:2] == bytes((0xD0, 0x30)):
        orig = orig[2:]
    return a.finish() + orig


def patch_one(data: bytes, experimental: bool) -> bytes:
    data = bytes(data)
    header_end = parse_rect(data, 8) + 4
    header = data[:header_end]
    tags = []
    abc_index = abc_name = abc_flags = abc_bytes = None
    for start, code, long_len, payload in iter_tags(data):
        if code == 82:
            flags = struct.unpack_from("<I", payload, 0)[0]
            z = payload.find(b"\x00", 4)
            name = payload[4:z].decode("utf-8", "replace")
            abcraw = payload[z + 1 :]
            if name == "frame2" or abc_bytes is None or len(abcraw) > len(abc_bytes or b""):
                abc_index = len(tags)
                abc_name = name
                abc_flags = flags
                abc_bytes = abcraw
        tags.append((code, payload))
    abc = Abc(abc_bytes)
    orig_s, orig_ns, orig_mn = len(abc.strings), len(abc.namespaces), len(abc.multinames)
    mid = find_game_update(abc)
    orig_code = abc.bodies[mid]
    print(f"  building menu experimental={experimental} orig_update={len(orig_code)}")
    new_code = build_code(abc, orig_code, experimental)
    print(f"  new update {len(new_code)} bytes, +str {len(abc.strings)-orig_s} +mn {len(abc.multinames)-orig_mn}")
    new_abc = apply_body_patch(abc_bytes, abc, orig_s, orig_ns, orig_mn, mid, new_code, 12, 14)
    tags[abc_index] = (82, struct.pack("<I", abc_flags) + abc_name.encode() + b"\x00" + new_abc)
    out = rebuild_swf(header, tags)
    if experimental:
        out = patch_fps_header(out, 120)
    return out


def main():
    print("load", SRC)
    base = load_swf(SRC)
    menu = patch_one(base, False)
    write_swf(ROOT / "orion_menu.swf", menu, compressed=True)
    exp = patch_one(base, True)
    write_swf(ROOT / "orion_experemental.swf", exp, compressed=True)
    print("ok")


if __name__ == "__main__":
    main()
