# EZ_MOD & Arma 3 Mod Manager

Набор инструментов для быстрого портирования снаряжения, формы, головных уборов, рюкзаков и аксессуаров в **Arma 3** (включая интеграцию с **Blender 5.x** и **ACE3 Arsenal Extended / XtdGear**).

## Состав проекта

- **`ArmaModManager.py` / `ArmaModManager.bat`** — локальный веб-интерфейс и 3D-инспектор (PBO / ODOL / MLOD, предпросмотр текстур и нормалей, отправка выбранной модели в 1 клик в Blender).
- **`ArmaPboTool.py` / `ArmaPboTool.bat`** — утилита для распаковки и анализа PBO-архивов.
- **`ez_mod_core.py`** — ядро пайплайна сборки модов в 1 клик (`Mesh -> MLOD -> PAA + RVMAT -> Binarize ODOL -> CfgConvert config.bin -> PBO`):
  - авто-распаковка DXT5nm нормалей (Tarkov / Unity) и очистка паразитного альфа-канала на диффузных текстурах;
  - генерация `*_ca.paa` и `Fresnel`-материала для стеклянных слотов (`EZ_Arma3_Glass`);
  - вшивание 17 прокси-треугольников и служебных LOD'ов (`soldier_tech_lods.bin`) для слота `UNIFORM` (костюм/форма).
- **`tools/ez_mod.py`** — панель **`EZ_MOD`** и **`SENT+`** для Blender (`ArmaToolbox`):
  - спавн манекена и живого скелета `OFP2_ManSkeleton` с тест-позами;
  - 1-клик импорт моделей из Инспектора или `.p3d` со всеми текстурами и настройками;
  - склейка мульти-UV (`Починить мульти-UV / Склеить ассеты`), адаптация нормалей (`0.45`), нода стекла (`EZ_Arma3_Glass`), авто-подгонка перчаток (`PCA/OBB + 30 костей пальцев`), авто-риг, `Weight Paint` в стиле Photoshop, авто-лодинг и `View Pilot` (скрытие в 1-м лице);
  - сборка вариантов для **ACE Arsenal Extended (`XtdGearModels` / `XtdGearInfo`)** в один PBO.

## Требования

- Python 3.10+ (с установленным `Pillow` и `numpy` для обработки текстур)
- **Arma 3 Tools** (`Pal2PacE.exe`, `Binarize.exe`, `CfgConvert.exe`)
- **Blender 5.1 / 5.2** (файл `tools/ez_mod.py` подключается к `ArmaToolbox`)

## Быстрый старт

1. Запустите `ArmaModManager.bat` (веб-интерфейс откроется на `http://127.0.0.1:48123`).
2. Скопируйте `tools/ez_mod.py` в папку расширения `ArmaToolbox` в Blender.
3. Используйте вкладку **`EZ_MOD`** в 3D Viewport для настройки, риггинга и сборки мода в 1 клик.
