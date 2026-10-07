# Matrix 8.8 — дождь со словами на панели Turing 8.8"

Тема для панели **Turing 8.8" V1.x (USB)** (480×1920): падающие символы, среди
которых раз за разом проступают заданные слова. Слово, скорости, цвета и шрифт
задаются в `config.toml` — файл темы пересобирается скриптом `generate.py`.

Проверено на панели `turzx_0001_0008` (Turing 8.8" V1.x, USB, 32 ГиБ карта),
прошивка отказывается играть видео из своей памяти, поэтому кадры идут в её
декодер потоком H.264 — для этого нужен патч bezel из `patches/`.

## Установка

```bash
# 1. тема
git clone https://github.com/vyorkin/bezel-matrix-8.8 \
  ~/.local/share/bezel/themes/matrix-8.8

# 2. патч bezel (подкоманда `bezel stream`)
git clone https://github.com/slipalison/bezel ~/Work/bezel
cd ~/Work/bezel && git checkout v0.16.0
git apply ~/.local/share/bezel/themes/matrix-8.8/patches/0001-stream-h264-to-turing-usb.patch
BEZEL_VERSION="0.16.0+matrix-stream" cargo build --release -p bezel
install -m 755 target/release/bezel ~/.local/bin/bezel

# 3. служба
mkdir -p ~/.config/systemd/user
cp ~/.local/share/bezel/themes/matrix-8.8/systemd/matrix-8.8.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now matrix-8.8
```

Патч распространяется на условиях GPL-3.0-or-later, как и сам bezel.

```
config.toml   настройки: слова, скорости, цвета, длина петли, поворот
generate.py   рисует кадры и собирает видео (ffmpeg), постер, theme.json, run.sh
assets/       matrix.h264 (то, что играет панель), matrix.mp4, poster.png
run.sh        команда запуска для службы (пишет generate.py)
theme.json    тема Bezel с видео-фоном и постером (см. «Почему поток»)
patches/      патч bezel, добавляющий подкоманду `bezel stream`
```

## Быстрый старт

```bash
cd ~/.local/share/bezel/themes/matrix-8.8
vim config.toml          # например, другие слова
./generate.py            # ~17 с: 2400 кадров, ~20 МиБ
systemctl --user restart matrix-8.8   # панель подхватит новый файл
```

Статус и журнал:

```bash
systemctl --user status matrix-8.8
journalctl --user -u matrix-8.8 -f
```

## Почему поток, а не тема

Схема темы Bezel (`theme.json`) описывает статичные элементы, поэтому движение
в ней возможно только фоном. Обычный путь — положить видео в память панели и
позволить ей играть его самой:

```bash
bezel storage put assets/matrix.mp4 sd/video/matrix.h264   # так задумано
bezel storage play sd/video/matrix.h264
```

**Эта прошивка так не умеет.** Проверено на панели `turzx_0001_0008` (2026-10-08):

| Что отправлено | Что ответила панель |
|---|---|
| `PLAY_VIDEO sd/video/matrix.h264` | `the screen refused PLAY_VIDEO …` |
| крошечный `.h264` (2.6 КиБ) | refused |
| ландшафтный 1920×480 | refused |
| внутренняя память `/usr/data/` | `0 B free`, залить нельзя |
| картинка `sd/image/*.png` | работает (`SHOW_IMAGE` принят) |

Вендорское приложение эти панели так и не использует: оно **стримит H.264 в
декодер панели** (команда 121). В bezel 0.16.0 этого пути не было, поэтому в
`patches/` лежит патч, добавляющий подкоманду `bezel stream`. `theme.json` и
`matrix.mp4` остаются в теме как запасной вариант: если прошивка когда-нибудь
начнёт играть файлы из памяти, тему можно будет включить обычным
`bezel run matrix-8.8`.

## Что нужно

- `ffmpeg` и `python3` с `Pillow` — для `generate.py`;
- патченый bezel с подкомандой `bezel stream` (шаг 2 установки выше).

## Настройки (`config.toml`)

### Слова

```toml
[word]
list = ["пиздец", "хуй", "не еби мозги"]
columns = [3, 7, 11]   # колонка для каждого слова, слева направо с нуля (0..14)
copies = 6             # сколько раз слово попадает на доску за цикл
trail = 30             # шлейф букв: длиннее шума, слово читается дольше
scale = 1.2            # буквы крупнее клетки и выезжают в соседние колонки
```

Слово падает в своей колонке и читается сверху вниз. Чем больше `copies`, тем
чаще оно появляется (за цикл, то есть за `video.cycle_seconds`). Если указать
меньше колонок, чем слов, слова поделят колонки по кругу.

### Видео

```toml
[video]
fps = 20               # панель играет 20 кадров в секунду
cycle_seconds = 40     # цикл дождя: за это время картинка возвращается в исходную
duration_seconds = 120 # длина файла; должно быть кратно циклу
fade_seconds = 0.5     # затухание в чёрное на концах файла
stream_turns = 2       # поворот кадров для потока (панель висит вверх ногами)
```

`cycle_seconds` — важное число: колонка повторяется только тогда, когда её
голова за цикл проходит и целое число своих досок, и целое число экранов
(60 клеток). `generate.py` подбирает скорости так, чтобы это выполнялось, — иначе
на стыке цикла картинка прыгала бы. При 40 с и диапазоне скоростей из `[rain]`
получаются скорости 0.225, 0.3, 0.375 и 0.45 клетки за кадр.

`fade_seconds` маскирует короткую паузу: панель делает её там, где кончается
файл и начинается следующий проход. Файл длиннее = пауза реже, затухание =
пауза приходится на тёмные кадры.

### Остальное

`[canvas]` — размер панели, `[grid]` — сетка и шрифт, `[colors]` — палитра
(фон, шум, слово, свечение), `[rain]` — число колонок, длина шлейфа, скорости,
сила свечения.

Полезные флаги `generate.py`:

```bash
./generate.py --preview 6        # 6 кадров и постер в /tmp/matrix-8.8-preview
./generate.py --word привет      # разово подменить слово, не правя config.toml
./generate.py --seed 42          # другой случайный узор шума
```

## Служба

`~/.config/systemd/user/matrix-8.8.service` запускает `run.sh`, а тот —
`bezel stream assets/matrix.h264 --fps 20 --rotation 2` (числа берутся из
`config.toml` при генерации).

Важно: **порт панели держит только один процесс**. Поэтому служба
`bezel-run@omarchy-8.8`, которая раньше рисовала на панели палитру Omarchy,
выключена и **замаскирована** (`systemctl --user disable
bezel-run@omarchy-8.8` и `systemctl --user mask bezel-run@omarchy-8.8`): когда
она была просто выключена, её кто-то включал обратно, и два процесса по очереди
отбирали друг у друга порт — панель в это время мигала темой Omarchy. Скрипт
`omarchy-rgb-sync` продолжает генерировать тему `omarchy-8.8`, но панель больше
не трогает — кроме яркости: его вызов `bezel brightness` будет отказывать, пока
идёт поток. Чтобы задать яркость панели:

```bash
systemctl --user stop matrix-8.8
bezel brightness 70
systemctl --user start matrix-8.8
```

Вернуть панель к палитре Omarchy:

```bash
systemctl --user disable --now matrix-8.8
systemctl --user unmask bezel-run@omarchy-8.8
systemctl --user enable --now bezel-run@omarchy-8.8
```

В репозиторий положены и сами `assets/matrix.h264` с `assets/matrix.mp4`
(~21 МиБ каждый, чтобы тему можно было запустить без `ffmpeg` и Python). Любая
правка `config.toml` их перезапишет: они полностью выводятся из конфига.

## Что известно про эту панель (измерено 2026-10-08)

- `turzx_0001_0008`, «Turing 8.8" V1.x (USB)», карта памяти — 32 ГиБ, внутренняя
  память в протоколе отвечает нулями.
- Без команды `SET_ROTATION` (13) в преамбуле панель принимает чанки и **не
  играет их**: картинка застывает. С командой — играет.
- Чанки должны быть того размера, который панель называет сама
  (`GET_CHUNK_SIZE`, здесь ~200 КиБ): 32 КиБ она показывает тяжёлыми рывками.
- Кормить нужно ровно со скоростью видео. Меньше — пауза между порциями, больше
  (даже +12 %) — артефакты и рвань: декодер не имеет запаса.
- Файлы на карте (`sd/video/matrix.h264`, `tiny.h264`, `land.h264`,
  `sd/image/green.png`) остались от опытов: `bezel` не умеет удалять файлы с
  экранов этого семейства. Они безвредны (~3 МиБ), удалить можно с карты в
  картридере.

## Патч bezel

`patches/0001-stream-h264-to-turing-usb.patch` — против тега `v0.16.0`:

- `bezel-core`: порт `ScreenLink::stream_video` и настройки `VideoStream`;
- `bezel-devices`: реализация в драйвере `turing_usb` (преамбула, чанки,
  контроль очереди декодера, реальный темп, остановка) и счётчик кадров
  Annex-B для расчёта темпа;
- `bezel`: подкоманда `bezel stream <FILE> [--fps N] [--rotation 0-3]
  [--brightness PERCENT] [--pace SECONDS] [--chunk BYTES]`.
