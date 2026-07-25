#!/usr/bin/env python3
"""Build the two aligned images used by the landing-page translation lens.

Source document:
  Vaswani et al., "Attention Is All You Need", NeurIPS 2017.
  https://papers.nips.cc/paper_files/paper/2017/file/
  3f5ee243547dee91fbd053c1c4a845aa-Paper.pdf

Render page 3 at 180 DPI before running this utility:

  pdftoppm -f 3 -l 3 -png -r 180 -singlefile \
    attention-neurips-2017.pdf attention-neurips-page-3
  python render_attention_demo.py \
    attention-neurips-page-3.png ../assets/demo

The original page remains untouched. The translated layer starts from the same
pixels and replaces only natural-language blocks; the architecture figure,
formulae, references, page geometry, and page number keep their source layout.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BASE_WIDTH = 1530
BASE_HEIGHT = 1980
BODY_FONT = "/System/Library/Fonts/Supplemental/Songti.ttc"
HEADING_FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
INK = (16, 21, 27)
PAPER = (255, 255, 255)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="180-DPI PNG of NeurIPS page 3")
    parser.add_argument("output_dir", type=Path)
    return parser.parse_args()


def scale_box(box: tuple[int, int, int, int], sx: float, sy: float) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return (
        round(left * sx),
        round(top * sy),
        round(right * sx),
        round(bottom * sy),
    )


def font(path: str, size: int, scale: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(8, round(size * scale)))


def draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    origin: tuple[int, int],
    text_font: ImageFont.FreeTypeFont,
    line_height: int,
    scale: float,
    fill: tuple[int, int, int] = INK,
) -> None:
    x, y = origin
    for line in lines:
        draw.text((x, y), line, font=text_font, fill=fill)
        y += round(line_height * scale)


def main() -> None:
    args = parse_args()
    source = Image.open(args.source).convert("RGB")
    width, height = source.size
    sx = width / BASE_WIDTH
    sy = height / BASE_HEIGHT
    font_scale = min(sx, sy)

    if abs(width / height - BASE_WIDTH / BASE_HEIGHT) > 0.002:
        raise SystemExit(f"Unexpected page aspect ratio: {width}x{height}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    original_path = args.output_dir / "attention-page-3-original.webp"
    translated_path = args.output_dir / "attention-page-3-zh.webp"

    source.save(original_path, "WEBP", quality=88, method=6)

    translated = source.copy()
    draw = ImageDraw.Draw(translated)

    # Mask only text regions. Generous white margins remove the source glyphs
    # while leaving the Transformer diagram and every structural anchor intact.
    masks = [
        (492, 1000, 1038, 1047),
        (252, 1088, 1278, 1244),
        (252, 1262, 1278, 1475),
        (252, 1495, 1278, 1676),
        (252, 1692, 1278, 1822),
    ]
    for mask in masks:
        draw.rectangle(scale_box(mask, sx, sy), fill=PAPER)

    body = font(BODY_FONT, 22, font_scale)
    body_small = font(BODY_FONT, 21, font_scale)
    heading = font(HEADING_FONT, 27, font_scale)
    caption = font(BODY_FONT, 22, font_scale)

    caption_text = "图 1：Transformer 模型架构。"
    caption_box = draw.textbbox((0, 0), caption_text, font=caption)
    caption_width = caption_box[2] - caption_box[0]
    draw.text(
        ((width - caption_width) / 2, round(1008 * sy)),
        caption_text,
        font=caption,
        fill=INK,
    )

    draw_lines(
        draw,
        [
            "逐位置全连接前馈网络。我们在每个子层周围采用残差连接 [10]，",
            "随后进行层归一化 [1]。也就是说，每个子层的输出为",
            "LayerNorm(x + Sublayer(x))，其中 Sublayer(x) 表示该子层实现的函数。",
            "为便于这些残差连接，模型中的所有子层以及嵌入层都输出维度为",
            "d_model = 512 的表示。",
        ],
        (round(270 * sx), round(1098 * sy)),
        body,
        28,
        font_scale,
    )

    draw_lines(
        draw,
        [
            "解码器：解码器同样由 N = 6 个相同层堆叠而成。除编码器层中的",
            "两个子层外，解码器还插入第三个子层，对编码器堆栈的输出执行",
            "多头注意力。与编码器类似，各子层周围都使用残差连接，随后进行",
            "层归一化。我们还修改了解码器堆栈中的自注意力子层，防止当前位置",
            "关注后续位置。该掩码与输出嵌入错开一个位置的设计共同保证：",
            "位置 i 的预测只能依赖于位置小于 i 的已知输出。",
        ],
        (round(270 * sx), round(1274 * sy)),
        body,
        29,
        font_scale,
    )

    draw.text(
        (round(270 * sx), round(1504 * sy)),
        "3.2　注意力",
        font=heading,
        fill=INK,
    )
    draw_lines(
        draw,
        [
            "注意力函数可以描述为：将一个查询和一组键值对映射为输出，其中",
            "查询、键、值和输出都是向量。输出是各个值的加权和；分配给每个",
            "值的权重，由查询与对应键之间的兼容性函数计算得到。",
        ],
        (round(270 * sx), round(1552 * sy)),
        body,
        29,
        font_scale,
    )

    draw.text(
        (round(270 * sx), round(1700 * sy)),
        "3.2.1　缩放点积注意力",
        font=heading,
        fill=INK,
    )
    draw_lines(
        draw,
        [
            "我们将这种注意力称为“缩放点积注意力”（图 2）。其输入由维度",
            "为 d_k 的查询和键，以及维度为 d_v 的值组成。随后计算点积……",
        ],
        (round(270 * sx), round(1750 * sy)),
        body_small,
        28,
        font_scale,
    )

    translated.save(translated_path, "WEBP", quality=90, method=6)
    print(original_path)
    print(translated_path)


if __name__ == "__main__":
    main()
