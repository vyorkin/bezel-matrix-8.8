#!/usr/bin/env python3
"""Render the Matrix 8.8 theme: the falling-glyph video and its poster.

Everything comes from config.toml next to this script:

  assets/matrix.mp4   H.264, yuv420p, no audio, no B-frames — what the panel plays
  assets/poster.png   the frame where the word is fully lit (used when the panel
                      does not have the video yet)
  theme.json          the Bezel theme pointing at the video and the poster

The word from config.toml falls down one column and is read top to bottom. Its
letters are brighter than the noise and keep a longer trail, so the word stays
legible while the head moves on.

The video loops without a seam: every column returns to its exact starting state
on the last frame, so the panel can repeat it forever.

Usage:
  ./generate.py                  # full video, poster and theme.json
  ./generate.py --preview 6      # only 6 still frames into /tmp, for eyeballing
  ./generate.py --word привет    # override the word without editing config.toml
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"

# How many brightness steps a glyph is pre-rendered in. 24 is plenty for a trail
# that is already fading: more levels cost memory and buy nothing visible.
LEVELS = 24
GLOW_LEVELS = 12


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

def load_config(path: Path, word_override: str | None) -> dict:
    with path.open("rb") as fh:
        cfg = tomllib.load(fh)
    if word_override:
        cfg["word"]["list"] = [word_override]
    if not [w for w in cfg["word"]["list"] if w]:
        sys.exit("config.toml: список слов пуст")
    return cfg


def resolve_font(name: str) -> Path:
    """Font from this theme, from themes/fonts next door, or an absolute path."""
    for candidate in (HERE / name, HERE.parent / name, Path(name)):
        if candidate.is_file():
            return candidate
    sys.exit(f"не найден шрифт {name!r}")


def parse_color(text: str) -> tuple[int, int, int]:
    value = text.lstrip("#")
    if len(value) not in (6, 8):
        sys.exit(f"цвет {text!r} должен быть #rrggbb")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------- #
# glyphs
# --------------------------------------------------------------------------- #

class Glyphs:
    """Glyph masks rendered once, then reused at every brightness and blur."""

    def __init__(self, font: Path, size: int, cell_w: int, cell_h: int,
                 glow_radius: int, alphabet: str, mask_w: int | None = None,
                 mask_h: int | None = None):
        self.font = ImageFont.truetype(str(font), size)
        self.cell_w = cell_w
        self.cell_h = cell_h
        self.mask_w = mask_w or cell_w
        self.mask_h = mask_h or cell_h
        self.glow_radius = glow_radius
        self._masks: dict[str, Image.Image] = {}
        self._scaled: dict[tuple[str, int], Image.Image] = {}
        self._glow: dict[tuple[str, int], Image.Image] = {}
        self.alphabet = "".join(self._usable(ch) for ch in dict.fromkeys(alphabet))

    def _usable(self, ch: str) -> str:
        # A glyph the font does not have would print as an empty box; keep the
        # alphabet to what JetBrains Mono actually draws.
        return ch if self.font.getmask(ch).getbbox() else ""

    def mask(self, ch: str) -> Image.Image:
        img = self._masks.get(ch)
        if img is None:
            img = Image.new("L", (self.mask_w, self.mask_h), 0)
            draw = ImageDraw.Draw(img)
            draw.text(
                (self.mask_w / 2, self.mask_h * 0.78),
                ch,
                font=self.font,
                fill=255,
                anchor="ms",  # общая базовая линия для всех глифов
            )
            self._masks[ch] = img
        return img

    def scaled(self, ch: str, level: int) -> Image.Image:
        """The glyph mask at one of LEVELS brightness steps."""
        key = (ch, level)
        img = self._scaled.get(key)
        if img is None:
            factor = level / (LEVELS - 1)
            img = self.mask(ch).point(lambda v, f=factor: int(v * f))
            self._scaled[key] = img
        return img

    def glow(self, ch: str, level: int) -> Image.Image:
        key = (ch, level)
        img = self._glow.get(key)
        if img is None:
            factor = level / (GLOW_LEVELS - 1)
            img = (
                self.mask(ch)
                .filter(ImageFilter.GaussianBlur(self.glow_radius))
                .point(lambda v, f=factor: int(v * f))
            )
            self._glow[key] = img
        return img


# --------------------------------------------------------------------------- #
# rain
# --------------------------------------------------------------------------- #

@dataclass
class Column:
    index: int
    board: list[tuple[str, bool]]  # (glyph, is a letter of the word)
    phase: float                   # where the head starts, in board rows
    speed: float                   # board rows per frame
    trail: int                     # trail length in cells
    word: bool
    starts: list[tuple[int, str]]  # where each copy of a word sits (board row, word)


def build_columns(cfg: dict, rng: random.Random) -> tuple[list[Column], int]:
    """The rain columns, and the picture's row count.

    A column falls over a *board* of glyphs: the head walks it at `speed` cells
    per frame and the cell it left behind keeps its glyph while fading out. The
    board repeats, and so does the screen (60 cells tall), so the picture comes
    back to where it started only when the head has travelled a whole number of
    both — that is what closes the loop: the file may then be any whole number
    of `video.cycle_seconds`, and every seam in it is an ordinary step.
    """
    grid = cfg["grid"]
    rain = cfg["rain"]
    canvas = cfg["canvas"]
    video = cfg["video"]

    cell_h = int(grid["cell_height"])
    cols = int(rain["columns"])
    rows = int(canvas["height"]) // cell_h
    fps = int(video["fps"])
    cycle = int(round(float(video.get("cycle_seconds", video["duration_seconds"])) * fps))
    speed_min, speed_max = float(rain["speed_min"]), float(rain["speed_max"])

    alphabet = rain["alphabet"]
    by_column = words_by_column(cfg, cols)

    def speeds(board_len: int, most: int = 8) -> list[float]:
        """The speeds that close a loop over a board of `board_len` cells: the
        head travels a whole number of boards in one cycle."""
        return [
            board_len * k / cycle
            for k in range(1, most + 1)
            if speed_min <= board_len * k / cycle <= speed_max
        ]

    columns: list[Column] = []
    for index in range(cols):
        starts: list[tuple[int, str]] = []
        if index in by_column:
            here = by_column[index]
            longest = max(len(word) for word in here)
            copies = max(1, int(cfg["word"].get("copies", 4)))
            slots = copies * len(here)
            # A board holding every word `copies` times, evenly spaced, a whole
            # number of screens long (2..12) and with room in each slot for the
            # longest word plus a little noise. Of those, the speeds that close
            # the loop decide.
            candidates = [
                rows * m
                for m in range(2, 13)
                if rows * m // slots >= longest + 3 and speeds(rows * m)
            ]
            if not candidates:
                sys.exit(
                    "слова не укладываются в цикл: увеличьте video.cycle_seconds, "
                    "уменьшите word.copies или укоротите слова"
                )
            board_len = rng.choice(candidates)
            chosen = rng.choice(speeds(board_len))
            step = board_len // slots
            letters: dict[int, str] = {}
            for slot in range(slots):
                word = here[slot % len(here)]
                start = slot * step + (step - len(word)) // 2
                starts.append((start % board_len, word))
                for k, ch in enumerate(word):
                    letters[(start + k) % board_len] = ch
            board = [
                (letters[y], True) if y in letters else (rng.choice(alphabet), False)
                for y in range(board_len)
            ]
        else:
            # A noise column: two to four screens of glyphs, so the same ones
            # do not come round too soon.
            lengths = [rows * n for n in (2, 3, 4) if speeds(rows * n)]
            if not lengths:
                sys.exit("rain.speed_min/speed_max не дают ни одной скорости при этом цикле")
            board_len = rng.choice(lengths)
            chosen = rng.choice(speeds(board_len))
            board = [(rng.choice(alphabet), False) for _ in range(board_len)]
        columns.append(
            Column(
                index=index,
                board=board,
                phase=rng.uniform(0.0, board_len),
                speed=chosen,
                trail=rng.randint(int(rain["trail_min"]), int(rain["trail_max"])),
                word=index in by_column,
                starts=starts,
            )
        )
    return columns, rows


def words_by_column(cfg: dict, columns: int) -> dict[int, list[str]]:
    """Which words fall in which column. `word.columns` names them; a word more
    than the columns share one, and without any named column every word falls
    down the middle, spaced out from the next."""
    words = [word for word in cfg["word"]["list"] if word]
    if not words:
        sys.exit("config.toml: список слов пуст")
    requested = [int(c) for c in cfg["word"].get("columns", [])]
    if not requested:
        middle = columns // 2
        step = max(1, columns // (len(words) + 1))
        requested = [middle + (i - len(words) // 2) * step for i in range(len(words))]
    spread: dict[int, list[str]] = {}
    for i, word in enumerate(words):
        column = requested[i % len(requested)] % columns
        spread.setdefault(column, []).append(word)
    return spread


def render_frame(t: int, columns: list[Column], glyphs: Glyphs, cfg: dict,
                 rows: int, glow: bool = True,
                 word_glyphs: Glyphs | None = None) -> Image.Image:
    colors = cfg["colors"]
    background = parse_color(colors["background"])
    noise_body = parse_color(colors["noise_body"])
    noise_head = parse_color(colors["noise_head"])
    word_body = parse_color(colors["word_body"])
    word_head = parse_color(colors["word_head"])
    glow_color = parse_color(colors["glow"])
    glow_strength = float(rain_of(cfg, "glow_strength", 0.55))
    word_trail = int(cfg["word"]["trail"])

    cell_w = int(cfg["grid"]["cell_width"])
    cell_h = int(cfg["grid"]["cell_height"])

    frame = Image.new("RGB", (cell_w * len(columns), rows * cell_h), background)

    lit: list[tuple[int, int, str, float, bool, bool]] = []
    for col in columns:
        head = int(col.phase + t * col.speed)
        board_len = len(col.board)
        limit = max(col.trail, word_trail)
        for age in range(limit + 1):
            y = head - age
            ch, is_letter = col.board[y % board_len]
            trail = word_trail if is_letter else col.trail
            if age > trail:
                continue
            alpha = (1.0 - age / trail) ** 1.6
            lit.append((col.index * cell_w, (y % rows) * cell_h, ch, alpha, age == 0, is_letter))

    # Слово рисуется крупнее клетки, поэтому оно выезжает в соседние колонки и
    # читается даже боковым зрением; шум остаётся в своих клетках.
    def sprite_for(is_letter: bool) -> Glyphs:
        if is_letter and word_glyphs is not None:
            return word_glyphs
        return glyphs

    def place(gs: Glyphs, x: int, y: int) -> tuple[int, int]:
        return x - (gs.mask_w - cell_w) // 2, y - (gs.mask_h - cell_h) // 2

    if glow:
        # Свечение отдельным проходом: если рисовать его поверх уже поставленных
        # глифов, соседние клетки смазываются.
        for x, y, ch, alpha, _, is_letter in lit:
            strength = glow_strength * (alpha ** 2)
            if is_letter:
                strength = min(1.0, strength * 2.4)
            elif alpha < 0.55:
                continue
            level = max(1, int(strength * (GLOW_LEVELS - 1)))
            gs = sprite_for(is_letter)
            px, py = place(gs, x, y)
            frame.paste(glow_color, (px, py), gs.glow(ch, level))

    for x, y, ch, alpha, is_head, is_letter in lit:
        gs = sprite_for(is_letter)
        px, py = place(gs, x, y)
        if is_letter:
            color = word_head if is_head else word_body
            floor = 0.30  # буквы слова не гаснут в ноль: слово видно дольше
        else:
            color = noise_head if is_head else noise_body
            floor = 0.0
        level = max(1, int((floor + (1 - floor) * alpha) * (LEVELS - 1)))
        frame.paste(color, (px, py), gs.scaled(ch, level))

    return frame


def rain_of(cfg: dict, key: str, default):
    return cfg["rain"].get(key, default)


def word_config(cfg: dict, key: str, default):
    return cfg["word"].get(key, default)


def poster_moment(column: Column, cfg: dict) -> int:
    """A frame where the head has just passed the last letter of a word."""
    board_len = len(column.board)
    start, word = column.starts[0]
    target = (start + len(word) + 2) % board_len
    # head(t) = phase + t * speed  →  solve for the head landing on target
    delta = (target - column.phase) % board_len
    return int(round(delta / column.speed))


# --------------------------------------------------------------------------- #
# outputs
# --------------------------------------------------------------------------- #

def write_theme_json(cfg: dict, word: str) -> None:
    canvas = cfg["canvas"]
    name = f"Matrix 8.8 — {word}"
    body = f"""{{
  "schema": 1,
  "name": {json_str(name)},
  "canvas": {{ "width": {int(canvas['width'])}, "height": {int(canvas['height'])} }},
  "orientation": {json_str(canvas['orientation'])},
  "refreshSeconds": 1.0,
  "background": {{
    "type": "video",
    "asset": "assets/matrix.mp4",
    "poster": "assets/poster.png"
  }},
  "elements": []
}}
"""
    (HERE / "theme.json").write_text(body, encoding="utf-8")


def json_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def encode(frames, cfg: dict, out: Path) -> None:
    """Encodes a frame stream as H.264 (yuv420p, no audio, no B-frames — what
    the panel's decoder takes): a raw Annex-B file for `.h264`, an MP4
    otherwise (the theme's video background)."""
    canvas = cfg["canvas"]
    video = cfg["video"]
    width, height = int(canvas["width"]), int(canvas["height"])
    fps = int(video["fps"])
    # An MP4 carries the moov atom first so a screen can start at its first
    # byte; a raw stream has no container at all.
    container = (
        ["-f", "h264"]
        if out.suffix == ".h264"
        else ["-f", "mp4", "-movflags", "+faststart"]
    )
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-an", "-sn", "-dn",
        "-c:v", "libx264", "-preset", str(video["preset"]), "-crf", str(video["crf"]),
        "-pix_fmt", "yuv420p", "-profile:v", "main", "-level", "4.0",
        "-bf", "0",  # сопроцессор панели не переваривает B-кадры
        "-g", str(fps * 2),  # ключевой кадр раз в две секунды
        *container,
        str(out),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for frame in frames:
            process.stdin.write(frame.tobytes())
        process.stdin.close()
    except BrokenPipeError:
        pass
    if process.wait() != 0:
        sys.exit(f"ffmpeg не смог закодировать {out.name}")


def frames_for(cfg: dict, columns, glyphs, word_glyphs, rows: int, frames: int, turns: int):
    """One pass of the file, turned `turns` quarter turns clockwise. The file's
    own ends fade to black: the panel pauses where a stream stops and starts
    again, and a pause on dark frames reads as a blink."""
    transpose = {
        1: Image.Transpose.ROTATE_270,
        2: Image.Transpose.ROTATE_180,
        3: Image.Transpose.ROTATE_90,
    }
    video = cfg["video"]
    fade = int(round(float(video.get("fade_seconds", 0)) * int(video["fps"])))
    turns %= 4
    for index in range(frames):
        # The rain cycles every `cycle_seconds` whatever the file's length is:
        # a file of whole cycles repeats the very frames it started with.
        cycle = int(round(float(video.get("cycle_seconds", 0)) * int(video["fps"])))
        t = index % cycle if cycle > 0 else index
        frame = render_frame(t, columns, glyphs, cfg, rows, word_glyphs=word_glyphs)
        if turns:
            frame = frame.transpose(transpose[turns])
        yield fade_frame(frame, index, frames, fade)


def fade_frame(frame: Image.Image, index: int, frames: int, fade: int) -> Image.Image:
    """`frame` darkened towards black at the file's first and last `fade`
    frames, so the file's seam sits on black."""
    if fade <= 0 or (index >= fade and index < frames - fade):
        return frame
    factor = index if index < fade else max(0, frames - 1 - index)
    return Image.blend(Image.new("RGB", frame.size), frame, factor / fade)


def write_run_script(cfg: dict) -> None:
    """A launcher the service unit runs. It carries the settings the video was
    generated with (frame rate, rotation), so re-generating keeps them in step
    and nobody has to remember them."""
    video = cfg["video"]
    script = HERE / "run.sh"
    script.write_text(
        "#!/bin/sh\n"
        "# Дождь Matrix на панели Turing 8.8\". Файл пишет generate.py: он повторяет\n"
        "# настройки, с которыми собран assets/matrix.h264.\n"
        'exec "${BEZEL:-$HOME/.local/bin/bezel}" stream \\\n'
        f'  "$(dirname "$0")/assets/matrix.h264" --fps {int(video["fps"])}\n',
        encoding="utf-8",
    )
    script.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--word", help="слово вместо значения из config.toml")
    parser.add_argument("--config", default=str(HERE / "config.toml"))
    parser.add_argument("--seed", type=int, default=None, help="фиксированный шум")
    parser.add_argument("--preview", type=int, default=0,
                        help="только N кадров-картинок в /tmp/matrix-8.8-preview")
    args = parser.parse_args()

    cfg = load_config(Path(args.config), args.word)
    font = resolve_font(cfg["grid"]["font"])
    glyphs = Glyphs(
        font=font,
        size=int(cfg["grid"]["font_size"]),
        cell_w=int(cfg["grid"]["cell_width"]),
        cell_h=int(cfg["grid"]["cell_height"]),
        glow_radius=int(rain_of(cfg, "glow_radius", 7)),
        alphabet=cfg["rain"]["alphabet"],
    )
    word_scale = float(word_config(cfg, "scale", 1.3))
    word_glyphs = Glyphs(
        font=font,
        size=int(round(int(cfg["grid"]["font_size"]) * word_scale)),
        cell_w=int(cfg["grid"]["cell_width"]),
        cell_h=int(cfg["grid"]["cell_height"]),
        glow_radius=int(rain_of(cfg, "glow_radius", 7)),
        alphabet="".join(cfg["word"]["list"]),
        mask_w=int(round(int(cfg["grid"]["cell_width"]) * word_scale)) + 2,
        mask_h=int(round(int(cfg["grid"]["cell_height"]) * word_scale)) + 2,
    )
    rng = random.Random(args.seed if args.seed is not None else " ".join(cfg["word"]["list"]))
    columns, rows = build_columns(cfg, rng)
    frames = int(cfg["video"]["fps"] * cfg["video"]["duration_seconds"])
    word_col = next(col for col in columns if col.word)

    ASSETS.mkdir(exist_ok=True)

    if args.preview:
        out = Path("/tmp/matrix-8.8-preview")
        out.mkdir(exist_ok=True)
        step = max(1, frames // args.preview)
        for i in range(args.preview):
            t = i * step
            render_frame(t, columns, glyphs, cfg, rows, word_glyphs=word_glyphs).save(
                out / f"frame{i:02d}.png"
            )
        render_frame(
            poster_moment(word_col, cfg), columns, glyphs, cfg, rows,
            word_glyphs=word_glyphs,
        ).save(out / "poster.png")
        print(f"{args.preview} кадров и постер в {out}")
        return 0

    print(f"слова: {', '.join(cfg['word']['list'])!r}; колонки: "
          f"{sorted(c.index for c in columns if c.word)} из {len(columns)}")
    render_frame(
        poster_moment(word_col, cfg), columns, glyphs, cfg, rows,
        word_glyphs=word_glyphs,
    ).save(ASSETS / "poster.png")
    print(f"постер: {ASSETS / 'poster.png'}")

    encode(
        frames_for(cfg, columns, glyphs, word_glyphs, rows, frames, 0),
        cfg,
        ASSETS / "matrix.mp4",
    )
    turns = int(cfg["video"].get("stream_turns", 0)) % 4
    encode(
        frames_for(cfg, columns, glyphs, word_glyphs, rows, frames, turns),
        cfg,
        ASSETS / "matrix.h264",
    )
    write_theme_json(cfg, ", ".join(cfg["word"]["list"]))
    write_run_script(cfg)
    for name in ("matrix.mp4", "matrix.h264"):
        size = (ASSETS / name).stat().st_size / 1024 / 1024
        print(f"видео:  {ASSETS / name} ({size:.1f} MiB, {frames} кадров)")
    print(f"тема:   {HERE / 'theme.json'}")
    print(f"запуск: {HERE / 'run.sh'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
