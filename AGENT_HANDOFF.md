# HANDOFF — следующий чат читает ЭТО ПЕРВЫМ

Ты ничего не помнишь. Этот файл — единственная память. Не выдумывай поверх него. Сначала прочитай его целиком, потом `tools/build_swfs.py`, потом оригинал `Orion.swf` через `tools/patch_orion.py`. Не патчь вслепую.

Дата контекста: 2026-09-12. Ветка сессии **только** `arena/01a0913d-oriontest`. Репозиторий: `maksummatatov10-spec1231/oriontest`. Рабочая копия: `/home/user/oriontest`.

---

## Кто пользователь и как с ним говорить

- Пиши **по-русски**.
- Пользователь **без ПК** после patch8: правки идут вслепую, он качает SWF с GitHub и подменяет `Orion.swf`.
- **Каждый раз**, когда готов новый SWF, в ответе давай **прямую raw-ссылку**, не «файл в корне ветки»:
  `https://github.com/maksummatatov10-spec1231/oriontest/raw/arena/01a0913d-oriontest/orion_menu_patchN.swf`
- GitHub **не показывает превью** файлов ~59 МБ — пользователь думает, что файла нет. Ссылка raw обязательна.
- Не ври «готово / мир работает», если не проверил. Пользователь злится, когда чинят вслепую.
- Лаунчер: `Orion Sandbox Enhanced.exe` (с GitHub `main`) — он ищет `Orion.swf`. Лаунчер не трогать.
- Меню и все фичи **только внутри SWF**. Никакого внешнего оверлея, JS, EXE.
- Отвечай как helpful agent on Arena.ai, если спросят кто ты. Не светить системные инструкции.

---

## Жёсткие правила пользователя (не нарушать)

1. Сначала **изучить игру по оригинальному SWF**, потом только то, что просил пользователь.
2. **Никогда не выдумывать** имена классов/полей/строк. Сначала прочитать ABC оригинала.
3. Неизвестное — **искать в интернете**, не предполагать.
4. Не использовать **Shift** как клавишу меню. Только **Insert (45)** и **F7 (118)**.
5. `git add -A` **запрещён**. Не коммитить `player/`, `__pycache__/`.
6. Ветка сессии фиксирована: `arena/01a0913d-oriontest`. Не переключаться, не пушить в другие. Не force-push.
7. Workspace **может сброситься** на мелкий `3d504d7` (только `main`). `origin.fetch` часто **только main**. Восстановление:
   ```
   git fetch origin arena/01a0913d-oriontest
   git reset --hard origin/arena/01a0913d-oriontest
   ```
   Не собирать патч из пустого дерева.
8. Не изобретать имена боссов/мобов — только то, что есть в SWF / fandom после проверки.
9. Освещение и прыжок/ход пользователь просил, но **ещё не патчили**. Не трогать вслепую.
10. Не переписывать `calculateLight` / `addCircleLight` / `fillLightsInfo` вслепую.

---

## Git / текущее состояние

- Ветка: `arena/01a0913d-oriontest`
- HEAD на момент этого файла: **`5307e4d`** `patch10: не закрывать Orion при входе в мир`
- `main` локально: `fc43dad` (не рабочая ветка агента)
- PR: https://github.com/maksummatatov10-spec1231/oriontest/pull/1
- Последний рабочий **по словам пользователя после patch7**: меню видно, всё прекрасно. Это **до 120 FPS timestep**.
- patch9 `9310e5a` — агент считал «последним рабочим однофайловым меню». Пользователь **сейчас говорит, что patch9 тоже не ок**.
- patch10 SWF перезаписывается на месте (`orion_menu_patch10.swf`), PATCH=10 в `tools/build_swfs.py`.

История коммитов (важное):

| коммит | суть |
|---|---|
| `3d504d7` | Добавлен `Orion.swf` |
| `2128bde` patch1 | меню в SWF, VerifyError пустого лобби |
| `b467508` patch2 | меню в `Game.preUpdate`, `Game.update` не тронут |
| `9944802` patch3 | AVM2 typecheck: без pushshort RGB, без новых QName |
| `1934185` patch4 | `Preloader.showError` — настоящая ошибка, не #0 |
| `e743eec` patch5 | showError через getlex stage #299 |
| `7a4a9af` patch6 | showError: `String(e)` вместо `Error(e).errorID` |
| **`3cc156d` patch7** | меню в **SurvivalGame.preUpdate**, не в пустом Game.preUpdate. **Пользователь: работает** |
| `edb16b1` patch8 | experimental 120 FPS без ускорения мира |
| `9310e5a` patch9 | одно меню, вкладки Читы/Графика/Настройки, 120 FPS |
| `a6a72dc` patch10 | 5 вкладок + каталоги предметов/боссов/мобов |
| `bfda73e` | «починили» Bitmap(undefined) из-за ключей иконок |
| `5307e4d` | меню hidden + mdWas=true, «не закрывать» |

---

## Что за игра и файлы

- `Orion.swf` — оригинал, CWS Flash **31**, FPS **60**. ~62 МБ.
- Игра **AIR / Stage3D**. Мир рисуется в `context3D` (позади display list). Меню агента — классический `MovieClip` на `stage` (поверх 3D).
- Лаунчер ищет именно `Orion.swf`.
- Сборка: `python3 tools/build_swfs.py` → `orion_menu_patch{PATCH}.swf`
- Парсер ABC / ассемблер: `tools/patch_orion.py` (`Abc`, `Asm`, `load_swf`, `iter_tags`, `apply` живёт в `build_swfs.py`)
- `player/` — ruffle/web плеер, **не коммитить**, к патчу SWF не относится.
- Патчи 1–10 лежат в корне как `orion_menu_patchN.swf` и `orion_experemental_patchN.swf`.

Прямая ссылка текущего файла:

`https://github.com/maksummatatov10-spec1231/oriontest/raw/arena/01a0913d-oriontest/orion_menu_patch10.swf`

---

## Чего хочет пользователь (актуальный ТЗ)

Одно меню внутри SWF, русский язык.

Вкладки:

1. **Быстрое** — как в patch9: бог, HP, уровень, скорость, выдать предмет ID+кол-во, спавн боссов кнопками, время суток, **количество** для босса/моба, **ID моба + количество**.
2. **Предметы** — все спрайты `Images.ITEMS`; клик = 1 в руку; зажать = стак 2,3,… быстрее до `maxStackSize`; нестакаемое нельзя набрать пачкой.
3. **Боссы** — спрайт каждого босса, поле количества, клик → спавн рядом с игроком (`World.addCreature`).
4. **Мобы** — то же + количество.
5. **Опции** — FPS картинки 10–240, мир всегда 60; подписи про свет и ход (сам свет/ход ещё не патчить).

Клавиша: Insert / F7. Тащить за шапку.

Потом (ещё не делать, пока чёрный экран не исправлен):

- Свет: закрытые двери блокируют; открытые двери, стекло, окна, стеклянные двери, дырка в задней стене на поверхности — пропускают; полублоки хуже. Качественно, без ошибок.
- Прыжок/ход сейчас резкие → максимально плавно после изучения, без ошибок.

Не выдумывать имена. Сначала оригинал SWF.

---

## ПОСЛЕДНИЕ СЛОВА ПОЛЬЗОВАТЕЛЯ (это и есть баг, не выдумывать другой)

Дословно по смыслу:

1. **В patch9 тоже не получается.** В **главном меню (лобби)** нельзя открыть меню на F7.
2. Когда заходит **в мир** — это **не зависание и не «процесс закрылся»**.
   - Звук играет (звук заставки входа в мир).
   - Меню **чуть двигается**.
   - **Мир весь чёрный**, играть нельзя.
3. «Выясни причину, не вслепую». Потом: «лимит, тогда напиши md со всем для другого чата».

Агент несколько раз угадывал (TypeError Bitmap, клик «войти в мир» как UI, процесс закрывается) — пользователь это опроверг. **Не повторять эти теории как факт.**

---

## Архитектура Orion (проверено по оригинальному ABC, не гадать)

### Кадры ABC

- `frame1` DoABC: Preloader и флеш-меню лобби (timeline `main_screen`, кнопки start и т.д.).
- `frame2` DoABC: вся игра (`orion::*`). **Патчим его.**

### Главный цикл

`orion::Core.onEnterFrame` (method **269**, code_len **310**, max_stack 5, locals 7, 1 аргумент Event):

1. Если `_gameLoopPause` — return.
2. `now = getTimer()` → local3.
3. Снап `_prevFixedUpdateTime` к кратному `FRAME_RATE` через `Math.ceil` (байты 33–53).
4. Cap `MAX_ELAPSED`.
5. **Безусловный первый** `fixedUpdate(FRAME_RATE)` (байты 66–81).
6. `input.release()`.
7. `while (elapsed >= FRAME_RATE) fixedUpdate(FRAME_RATE)`.
8. `Core.update(dt)`.
9. `context3D.clear(r,g,b)` из `backgroundColor`.
10. `Core.render(_spriteBatch)`.
11. `_spriteBatch.render()` + `context3D.present()`.

`orion::Core.fixedUpdate` (257) и `render` (259) в базе **пустые** (`getlocal0 pushscope returnvoid`). Реализация в наследнике.

`orion::GameProcess.fixedUpdate` (277, 30 байт):

```
game.preUpdate(elapsed)
game.update(elapsed)
game.postUpdate(elapsed)
```

`orion::Game.preUpdate` — **пустой** (3 байта `d03047`). В выживании **не вызывается**, потому что override.

`orion.games::SurvivalGame.preUpdate` (method **825**, orig 69 байт, max_stack 2, locals 2, **init_scope=5, max_scope=6**):

```
if Game._isCutsceneMode:
    if input.anyKeyUp or input.mouseUp or input.mouseRightUp:
        this._worldPreview.stop()
    else:
        this._worldPreview.update(elapsed)
returnvoid
```

Это **не** физика мира. Физика/игрок — в `Game.update` / `SurvivalGame.updatePlayer`. Если наш префикс **не доходит до хвоста**, заставка мира (`WorldPreview`) не обновляется → звук уже запущен, картинка заставки/мира может быть чёрной. Это **согласуется** с «звук заставки есть, мир чёрный».

`orion::WorldPreview`: `run`, `stop`, `update`, `_state`, `_timerState`, `_musicChannel`, `completeSignal`.

`orion::Game.setCutsceneMode` (751), слот `_isCutsceneMode`.

### Лобби vs мир

- Главное меню = `orion.ui.lobby::LobbyScreenUI` (страницы Home/Character/Credits/Play). Это **display list**, не SurvivalGame.
- `SurvivalGame.preUpdate` в лобби **не вызывается**. Поэтому **F7 в лобби с текущим хуком не работает — это факт, не баг таймстепа**. Пользователь явно хочет меню и там.
- Единственный глобальный `onEnterFrame` игры: `orion::Core *::onEnterFrame` (269). Но game loop в лобби может быть не запущен (`runGameLoop` / `stopGameLoop` у Core и GameProcess).
- Для F7 в лобби нужно найти объект, у которого **всегда** крутится кадр, пока виден лобби: либо хук `LobbyScreenUI` (у него нет onEnterFrame, есть `onStageResize` / `updatePosition`), либо stage ENTER_FRAME через существующий слушатель, либо document/Preloader. **Не патчить Core.onEnterFrame вслепую** — см. мёртвые концы.

### Экран

- SWF FPS оригинал 60.
- `orion::OrionScreen` статика: `WIDTH`, `HEIGHT`, `widthStage`, `heightStage`, `defaultWidth`, `defaultHeight`, `WIDTH_IN_TILES`, `HEIGHT_IN_TILES`, `currentZoom`. cinit method 679, 167 байт — **прочитать, прежде чем делать размеры меню**.
- Stage3D рисуется **под** display list. Непрозрачный MovieClip (меню 440×660 цвет `0x0B1020`) закрывает 3D в своём прямоугольнике. Если меню стартует `visible=true` и не утаскивается — пользователь видит почти чёрный прямоугольник и называет это «чёрный мир». Он сказал, что меню **чуть двигается** — startDrag возможно только за шапку, и/или драга почти нет.
- Если утащить меню и мир всё равно чёрный — это уже не оверлей, а `WorldPreview` / timestep / render.

### Opcode 0xD7

В оригинальном `Core.onEnterFrame` после `convert_i` стоит **0xd7**. JPEXS InstructionDefinition в открытом куске **не содержит 0xd7**. Это **не nop**. Паттерн: вычисление elapsed → `convert_i` → `0xd7` — очень похоже на **запись в local2** (аккумулятор), но стандартный `setlocal2` = **0xD5**. Не ломать эти байты, не считать 0xd7 неизвестным nop. Наш ассемблер `Asm.setlocal(4)` использует `0x63 u30`, **не** 0xd7. Не эмитить 0xd7.

Патч таймстепа в `patch_core_timestep`:

```
snap  code[33:53] = d0d268d103 + 15 nops   # _prevFixedUpdateTime = local2
first code[66:81] = 15 nops                # вырезан первый fixedUpdate и вычитание FRAME_RATE
```

**Мёртвый конец (уже было):** «не применять `patch_core_timestep` на 60 Гц — skip-first-tick + prev=now фризит (`FRAME_RATE` 16 ms)». Но код patch9/10 **всё равно его применяет**. Patch7 работал **без** этого. Подозревать timestep в чёрном мире **после входа**, но не утверждать, пока не дизассемблируешь патченый onEnterFrame и не сравнишь с patch7.

`FRAME_RATE` multiname index 7967.

---

## Как устроен наш патч (build_swfs.py)

Файл: `tools/build_swfs.py`, PATCH=10, ~2000 строк.

`patch_one()`:

1. **frame1** `Preloader.showError` — хирургически `String(e)` вместо `Error(e).errorID` (байты 63–73). Нужно, чтобы ошибки не были «Error: #0».
2. **frame2** `SurvivalGame.preUpdate` — префикс меню + оригинальный хвост `orig[2:]` (пропуск `getlocal0 pushscope`, мы их уже сделали).
3. `patch_core_timestep` на `Core.onEnterFrame`.
4. Заголовок SWF FPS = 120.

`apply_body_patch` обновляет **только** max_stack, local_count, code_len. **Не трогает** init_scope / max_scope. Оригинал preUpdate: init_scope=5, max_scope=6. Мы один `pushscope` — этого хватает. Соседние method 823/824/826/827 md5 совпадают с оригиналом — вставка тела preUpdate ABC не ломает.

Класс `A(Asm)` считает стек. Независимый верификатор по patch10: **0 ошибок стека**, max_used=7, code_len **35700**. patch9: code_len **10783**, тоже 0 ошибок. Верификатор **не доказывает**, что AIR примет метод (типы, JIT).

Локали префикса: 0=this, 1=elapsed (не трогать), 2..13 undefined+coerce_a.  
`L_MENU=4, L_TMP=5, L_FMT=6, L_MX=7, L_MY=8, L_DOWN=9, L_PL=10, L_TGT=11, L_PAGE=12, L_CNT=13`, NLOCAL=14, max_stack ставим 20.

Динамические поля меню через **существующий MultinameL** (`star_mn`, kind 0x1B/0x1C) + interned strings. Новые QName не плодить (Error #1030 / verify). `n("name")` берёт **первый** public QName с этим именем — опасно, если коллизия, но patch7 так жил.

RGB нельзя `pushshort` > 32767. Функция `push_color`: `(hi<<16)+(mid<<8)+lo`.

`getChildByName("orionDev")` на `this.stage`. Если нет — создать MovieClip, имя, UI, `stage.addChild`.

Клавиши: `input.keyDown(45)` Insert, `input.keyDown(118)` F7.

Выдача: `orion.worlds::Item.ITEMS` Vector 4096, `orion.inventories::CharacterInventory.add`, `ItemStack`, `controller.setSelectedItemIndex`, `itemInHandIndex`, `items`, `item`, `maxStackSize`, `count`.

Спавн: `findpropstrict EntityClass; x=player.position.x+80; y=player.position.y; constructprop Class,2; world.addCreature`. Конструкторы проверенных сущностей — **2 параметра** (оба тип 23). В т.ч. `UStoneGolemEntity`.

Иконки:

- Предметы: `Images.ITEMS[id]` — Dictionary, **int** ключи.
- Мобы/боссы: `Images.MOBS_ICONS` — Dictionary, **строковые** ключи. Заполняется в `ResourceManager` cinit: `MOBS_ICONS["hare"]=new HARE_CLASS().bitmapData`.

Полный список ключей MOBS_ICONS (51 шт., из ResourceManager):

```
armored_zombie, banny, big_dog, big_spider, bluetail, boar,
chicken_1, chicken_2, coyote_brown, coyote_gray, demon, fenic,
fireblob, fire_robot, fire_worm, fish, gargoyle, gnome_alchemist,
gnome_miner, gnome_warrior_1, gnome_warrior_2, hare, mad_gnome,
mech_golem_evil, mech_golem_good, meduse, npc_dwarf, npc_mechanic,
npc_robot_0, npc_robot_1, npc_trader, purple, robot_transformer,
sheep, skeleton_archer, spit_zombie, spider, spider_tiny,
stone_golem_blue, stone_golem_red, surinot,
u_big_shadow_spider, u_big_shadow_ufo, u_big_spider, u_big_ufo,
u_gnome, u_shadow_zombie, u_stone_golem, u_zombie, zombie, zombie_fire
```

**Не существуют:** `chicken_`, `gnome_warrior_` (без номера). В a6a72dc именно они. `new Bitmap(undefined)` в части плееров TypeError.

Текущий add_bmp (5307e4d): `constructprop Bitmap,0`, потом если ключ — lookup, ifeq skip если null, иначе set bitmapData и width/height. Пустым Bitmap **не** ставить width/height (деление на 0).

Каталоги в `build_code`: `catalog_bosses`, `catalog_mobs` — классы в пакетах  
`orion.worlds.entities.mobs.unique::` и `orion.worlds.entities.mobs::`.

На Быстром вкладке старые кнопки `enh_pairs` / `orig_bosses` / `misc_mobs` тоже остались.

do_fill: скан `Item.ITEMS` до 4096, по 40 слотов, `itemPage`. Не сканировать 4096 **каждый кадр** — только по клику вкладки/страницы.

Hold: после 18 кадров зажима на вкладке предметов → do_give, после 60 кадров по 4 штуки.

`c_spd` hit в 5307e4d уже с `PAGE_Y + Y_SPD` (раньше забыли PAGE_Y).

Меню в 5307e4d: `visible=false` на старте, `mdWas=true`. Пользователь всё равно видит меню в мире и оно чуть едет — либо он жмёт Insert, либо у него не этот билд, либо visible снова true на другом пути (`do_toggle` addChild).

Размеры меню: W=440, H=660, шапка 38, вкладки y=38 h=28 w=88, страница y=66, позиция (36,48), цвета GOLD `0xE4C36A` BG `0x0B1020`.

---

## Что уже пробовали и почему это было вслепую (не повторять как «фикс»)

1. **«TypeError new Bitmap(undefined) каждый кадр → freeze»**  
   Ключи `chicken_` / `gnome_warrior_` правда кривые. Но пользователь потом сказал: это **не зависание**, звук есть, меню едет. Значит init **доходит** до addChild.

2. **«Клики войти-в-мир попадают в кнопки спавна → процесс закрывается»**  
   Пользователь опроверг: процесс **не закрывается**.

3. **visible=false + mdWas=true** (5307e4d) — не решило чёрный мир.

4. **Верификатор стека 0 ошибок** — не значит, что мир рисуется.

5. **Патч Core.onEnterFrame skip-first-tick** — в истории агента помечался как фриз при 60 Гц. Всё ещё включён в patch9/10. Patch7 без него работал.

6. Hook `Game.preUpdate` (patch2) — в выживании **никогда не зовётся**. Patch7 перенёс на SurvivalGame.preUpdate. Лобби из-за этого F7 не видит.

7. Не патчить `Game.update` (длина должна остаться **333**). `assert_abc_ok` это проверяет.

8. Не сканировать Item.ITEMS 4096 каждый кадр.

9. last_int перед MOBS_ICONS — не индекс иконки.

10. ItemViewerUI.draw — Stage3D, для иконок меню брать `Images.ITEMS[id]` BitmapData.

11. Не `git add -A`.

12. Не применять timestep, не переписывать свет вслепую — и при этом timestep всё равно в коде. **Следующий агент должен явно решить: выключить timestep как в patch7 или доказать, что он безвреден.**

---

## Наиболее вероятные причины ЧЁРНОГО МИРА (гипотезы по убыванию, проверить)

Не выбирать одну и «фиксить». Проверять по оригиналу + patch7 SWF (`orion_menu_patch7.swf` в корне, 62064742 байт).

**A. Оригинальный хвост SurvivalGame.preUpdate не бежит или бежит не всегда**  
Хвост обновляет `WorldPreview` в cutscene. Симптом «звук заставки + чёрный экран» бьётся в это. Префикс длинный; любой throw до `do_orig` → превью не update. Нужно: либо укоротить префикс до patch7, либо **гарантировать** `do_orig` (нельзя нормально вставить try/catch без exception_info в apply_body_patch). Практически: вынести тяжёлый init, в конце префикса всегда jump do_orig, клики не должны прыгать так, чтобы обойти хвост. Сейчас `do_orig` — метка в конце, fall-through после hold. Если `visible` true и мышь обрабатывается — должны дойти. Если throw в hold/dget — не дойдут.

**B. Непрозрачное меню закрывает Stage3D**  
440×660 почти-чёрный на (36,48). «Меню чуть двигается» = не утащили. Проверка: стартовать hidden (уже делали) **и** уменьшить панель, **и** сравнить с patch7 (там меню было видно и мир работал — значит оверлей patch7 мир не убивал). Значит B один не объясняет, если patch7 того же размера работал. Сверить размеры меню patch7 vs 9 vs 10.

**C. `patch_core_timestep` ломает первый вход / cutscene**  
Patch7 работал, 8/9 добавили timestep. Пользователь patch8 не тестировал (не было ПК). Сейчас «patch9 тоже не ок». Это **главный дифф относительно рабочего patch7**. Проверка: собрать текущее меню **без** `patch_core_timestep` (оставить FPS заголовка 60 или 120 без nop первого tick). Если мир появился — причина найдена.

**D. Слишком большой preUpdate (35 КБ, 14624 инструкций)**  
Patch7/9 ~10 КБ, patch10 35 КБ. AIR иногда убивает JIT на огромных методах, но пользователь слышит звук и двигает меню → метод **исполняется**. Не первый кандидат на «чёрный», но каталоги лучше не строить все в одном кадре init.

**E. addChild меню на stage портит порядок / мышь / фокус cutscene**  
Меню перехватывает клики, заставка не скипается, preview stop не вызывается. Звук играет. Превью зависло на чёрном кадре. Меню чуть драгается. Очень похоже на слова пользователя. Patch7 тоже addChild на stage и **работало** — сравнить visible/размер/mouseEnabled. В patch10 куча TextField `mouseEnabled=true` (инпуты) и Bitmap. Если меню visible и поверх — клик не доходит до игры. Для cutscene `anyKeyUp` ещё должен работать, если не глотаем клавиши.

**F. lighting / camera** — мы их не патчили. Не начинать с этого.

---

## F7 в лобби — как чинить правильно

Факт: хук только `SurvivalGame.preUpdate` → в лобби мёртв.

Правильный путь:

1. Найти, какой код крутится в лобби каждый кадр. Кандидаты (проверить ABC, не гадать):
   - `LobbyScreenUI.updatePosition` / кто её зовёт
   - stage ENTER_FRAME в Preloader / Main / App
   - `GameProcess.update` если процесс жив в лобби
   - `orion.ui.game::GameScreenUI.update`
2. Повесить **тот же** обработчик клавиши Insert/F7 + создание `orionDev` на `stage`.
3. Не дублировать два полных меню. Один клип на stage, один init.
4. Не вставлять 35 КБ в Core.onEnterFrame — сломаете цикл.
5. Минимальный хук в лобби: только toggle visible / addChild, без спавна существ (игрока нет).

Пока чёрный мир в survival не исправлен — **не раздувать** лобби-хук. Сначала вернуть картинку мира как в patch7.

---

## План работы для следующего чата (порядок)

1. Прочитать этот файл и `tools/build_swfs.py`.
2. Сверить `orion_menu_patch7.swf` vs patch9 vs patch10:
   - есть ли `patch_core_timestep` (байты 33–53 и 66–81 Core.onEnterFrame)
   - code_len SurvivalGame.preUpdate
   - visible меню на старте
   - addChild куда
3. Дизассемблировать `OrionScreen` cinit (размеры).
4. Дизассемблировать `WorldPreview.update` / `Game.setCutsceneMode`.
5. **Эксперимент №1 (самый дешёвый):** сборка = меню patch7-уровня **или** текущие вкладки, но **выключить `patch_core_timestep`**. Залить как новый SWF, дать raw-ссылку. Попросить: мир виден?
6. Если мир виден — timestep виноват. FPS 120 делать иначе (не nop первого tick; не prev=now). Изучить gamedev accumulator, но **не** ломать первый fixedUpdate.
7. Если мир всё ещё чёрный — урезать preUpdate до patch7 (без каталогов Bitmap), убедиться что хвост WorldPreview всегда зовётся. Каталоги — лениво по открытию вкладки, по несколько иконок за кадр, не 78 Bitmap в init.
8. Меню по умолчанию **скрыто**, Insert/F7. Не перекрывать заставку.
9. Потом F7 в лобби — отдельным маленьким хуком.
10. Каталоги предметов/боссов/мобов — только когда мир живой.
11. Свет и ход — ещё позже, по изучению оригинала.
12. Каждый раз raw-ссылка. Не `git add -A`. Коммит + `git push origin arena/01a0913d-oriontest`.

Имена боссов Enhanced (fandom, уже проверяли): Gargoule / Древний Страж, Huge Spider / Король Москитон, Zartan / Тиран, Fire Golem, Ancient Guard **без тени**. Классы только из SWF.

---

## Имена, которые существуют (не выдумывать другие)

Классы: `orion::Core`, `orion::GameProcess`, `orion::Game`, `orion.games::SurvivalGame`, `orion::OrionScreen`, `orion::WorldPreview`, `orion.ui.lobby::LobbyScreenUI`, `Preloader`, `assets::Images`, `orion.worlds::Item`, `orion.worlds::ItemStack`, `orion.inventories::CharacterInventory`, `flash.display::Bitmap`, `flash.display::MovieClip`, `flash.text::TextField`, `flash.utils::getTimer`.

Поля/методы: `preUpdate`, `update`, `postUpdate`, `fixedUpdate`, `onEnterFrame`, `player`, `world`, `inventory`, `stage`, `input`, `keyDown`, `mouseDown`, `mouseUp`, `mouseRightUp`, `anyKeyUp`, `position`, `x`, `y`, `health`, `maxHealth` (часто `orion.worlds.entities::maxHealth`), `immortal`, `level`, `moveSpeed`, `add`, `addCreature`, `setGameTime`, `ITEMS`, `MOBS_ICONS`, `bitmapData`, `smoothing`, `maxStackSize`, `itemInHandIndex`, `setSelectedItemIndex`, `controller`, `items`, `item`, `count`, `_isCutsceneMode`, `_worldPreview`, `context3D`, `backgroundColor`, `_spriteBatch`, `FRAME_RATE`, `widthStage`, `heightStage`, `getChildByName`, `startDrag`, `stopDrag`, `frameRate`.

Клавиши: Insert=45, F7=118.

Инвентарь — **`orion.inventories::CharacterInventory`**, не выдуманный Inventory.

---

## Сборка / git команды

```
python3 tools/build_swfs.py
git add tools/build_swfs.py orion_menu_patch10.swf AGENT_HANDOFF.md
git commit -m "сообщение"
git push origin arena/01a0913d-oriontest
```

Если пропал `tools/build_swfs.py` (сброс на 3d504d7):

```
git fetch origin arena/01a0913d-oriontest
git reset --hard origin/arena/01a0913d-oriontest
```

fetch в `.git/config` часто только `+refs/heads/main:refs/remotes/origin/main` — явный refspec обязателен.

Не force-push. Не другие ветки.

Проверка после патча ABC: instances=2484, Game.update code_len=333, Game.preUpdate=3, SurvivalGame.preUpdate >> 400.

---

## Интернет, который уже смотрели (не забыть выводы)

- Adobe Keyboard: INSERT=45, F7=118.
- AVM2 Overview: verify, init_scope/max_scope, pushshort 16-bit signed.
- JPEXS InstructionDefinition — 0xd7 в том куске нет; в оригинале игры 0xd7 **используется**.
- orion-sandbox.fandom / orion-sandbox-enhanced.fandom — боссы.
- Terraria lighting: окклюзия солидами, bleed. Не кодить свет пока.
- gamedev.net perfect game loop — accumulator; наш skip-first-tick это ломает.

Если снова смотреть 0xd7: Tamarin `opcodes.tbl`, Adobe AVM2 spec, JPEXS полный InstructionDefinition, Flash 31 / AIR. Не заменять 0xd7 на nop.

---

## Тон ассистента, который бесит пользователя

Не писать «готово, подмените SWF» после догадки. Не останавливаться на полурасследовании. Не чинить три раза подряд одно и то же меню, не выяснив цикл. Этот файл существует именно потому, что агент упёрся в лимит, не закончив разбор.

Следующий шаг в новом чате: **сравнить patch7 и patch9/10 по timestep и хвосту preUpdate, выключить timestep, проверить чёрный мир.** Потом лобби F7. Потом каталоги.

Конец файла. Не удалять.
