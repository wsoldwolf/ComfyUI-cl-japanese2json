"""Render the README block diagram as PNG and SVG (requires Pillow).

Run from any directory with Python. Windows defaults use Meiryo; other
environments can supply Japanese fonts with --font and --bold-font.
"""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT, SCALE = 1400, 840, 2
INK, MUTED, LINE = "#183247", "#4d6475", "#607c8e"
PALETTES = {
    "input": ("#edf5fc", "#aac8e3"),
    "process": ("#ebf7f3", "#96cabb"),
    "generate": ("#f2effb", "#b9a9df"),
    "output": ("#fff4d9", "#dfbf67"),
}

# x, y, width, height, palette, title lines, subtitle
BLOCKS = [
    (700, 40, 260, 86, "input", ["ユーザープロンプト"], "人物・世界観・演出の指示"),
    (50, 182, 260, 96, "input", ["参照画像"], None),
    (360, 182, 260, 96, "process", ["Image Analyzer"], "画像解析"),
    (700, 182, 260, 96, "process", ["Prompt Enhancer"], "プロンプトの統合・拡張"),
    (50, 344, 260, 100, "input", ["ボーカル + Lyrics"], None),
    (360, 344, 260, 100, "process", ["Vocal to Prompt", "Segments"], None),
    (700, 344, 260, 100, "process", ["Scene Limiter"], "対象シーンの選択"),
    (1090, 256, 260, 108, "process", ["MV Prompt Planner"], "シーンごとの演出を計画"),
    (1090, 430, 260, 90, "process", ["Japanese to JSON"], "H3用Planへコンパイル"),
    (50, 690, 220, 90, "input", ["フルミックス", "+ ボーカル"], None),
    (310, 690, 230, 90, "process", ["Audio Pad Pair"], "音声の尺を調整"),
    (580, 690, 230, 90, "generate", ["Contex Loop"], "シーンの連続生成を制御"),
    (850, 690, 270, 90, "generate", ["MiniMax H3 Ref2VA"], "映像・音声を生成"),
    (1160, 690, 190, 90, "output", ["完成MV"], None),
]

ARROWS = [
    [(830, 126), (830, 182)],
    [(310, 230), (360, 230)],
    [(620, 230), (700, 230)],
    [(960, 230), (1025, 230), (1025, 287), (1090, 287)],
    [(310, 394), (360, 394)],
    [(620, 394), (700, 394)],
    [(960, 394), (1045, 394), (1045, 332), (1090, 332)],
    [(1220, 364), (1220, 430)],
    [(1220, 520), (1220, 585), (695, 585), (695, 690)],
    [(270, 735), (310, 735)],
    [(540, 735), (580, 735)],
    [(810, 735), (850, 735)],
    [(1120, 735), (1160, 735)],
]


def render(output: Path, regular: Path, bold: Path) -> None:
    image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), "white")
    draw = ImageDraw.Draw(image)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
        '<title id="title">MV自動生成フロー</title>',
        '<desc id="desc">参照画像とユーザープロンプト、ボーカルと歌詞から演出を計画し、JSONへコンパイルする。フルミックスとボーカルはAudio Pad Pairで音声尺を調整する。Planと音声をContex Loopへ渡し、MiniMax H3 Ref2VAでMVを生成する概念図。参照画像からH3への接続等は省略。</desc>',
        '<rect width="1400" height="840" fill="white"/>',
        f'<g stroke="{LINE}" stroke-width="3" fill="none" stroke-linejoin="round">',
    ]
    for points in ARROWS:
        draw.line([(x * SCALE, y * SCALE) for x, y in points], fill=LINE,
                  width=3 * SCALE, joint="curve")
        svg.append('<polyline points="' + " ".join(f"{x},{y}" for x, y in points) + '"/>')
        (px, py), (x, y) = points[-2:]
        dx, dy = x - px, y - py
        length = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / length, dy / length
        arrow = [(x, y), (x - 11 * ux - 5 * uy, y - 11 * uy + 5 * ux),
                 (x - 11 * ux + 5 * uy, y - 11 * uy - 5 * ux)]
        draw.polygon([(int(ax * SCALE), int(ay * SCALE)) for ax, ay in arrow], fill=LINE)
        svg.append('<polygon stroke="none" fill="' + LINE + '" points="' +
                   " ".join(f"{ax:g},{ay:g}" for ax, ay in arrow) + '"/>')
    svg.append('</g>')

    for x, y, width, height, palette, titles, subtitle in BLOCKS:
        fill, border = PALETTES[palette]
        draw.rounded_rectangle((x * SCALE, y * SCALE, (x + width) * SCALE, (y + height) * SCALE),
                               radius=14 * SCALE, fill=fill, outline=border, width=2 * SCALE)
        svg.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="14" fill="{fill}" stroke="{border}" stroke-width="2"/>')
        entries = [(line, 23, bold, INK) for line in titles]
        if subtitle:
            entries.append((subtitle, 17, regular, MUTED))
        spacing = 32
        cy = y + height / 2 - spacing * (len(entries) - 1) / 2
        for text, size, font_path, color in entries:
            font = ImageFont.truetype(str(font_path), size * SCALE)
            # Catch accidental overflow when editing labels or translations.
            if draw.textlength(text, font=font) > (width - 20) * SCALE:
                raise ValueError(f"Label does not fit its block: {text}")
            draw.text(((x + width / 2) * SCALE, cy * SCALE), text,
                      font=font, fill=color, anchor="mm")
            weight = "700" if font_path == bold else "400"
            svg.append(f'<text x="{x + width / 2:g}" y="{cy:g}" text-anchor="middle" dominant-baseline="central" font-family="Meiryo, Noto Sans CJK JP, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(text)}</text>')
            cy += spacing
    svg.append('</svg>')
    output.mkdir(parents=True, exist_ok=True)
    image.save(output / "mv-generation-flow.png", optimize=True)
    (output / "mv-generation-flow.svg").write_text("\n".join(svg) + "\n", encoding="utf-8")
    print(f"Saved PNG and SVG to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/meiryo.ttc"))
    parser.add_argument("--bold-font", type=Path, default=Path("C:/Windows/Fonts/meiryob.ttc"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs" / "images")
    options = parser.parse_args()
    render(options.output_dir, options.font, options.bold_font)
