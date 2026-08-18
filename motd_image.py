import json
import os

from PIL import Image, ImageDraw

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")

CODES = {
    "0": (0, 0, 0),
    "1": (0, 0, 170),
    "2": (0, 170, 0),
    "3": (0, 170, 170),
    "4": (170, 0, 0),
    "5": (170, 0, 170),
    "6": (255, 170, 0),
    "7": (170, 170, 170),
    "8": (85, 85, 85),
    "9": (85, 85, 255),
    "a": (85, 255, 85),
    "b": (85, 255, 255),
    "c": (255, 85, 85),
    "d": (255, 85, 255),
    "e": (255, 255, 85),
    "f": (255, 255, 255),
}

STYLE_CODES = {"k": "obf", "l": "bold", "m": "strike", "n": "underline", "o": "italic", "r": "reset"}

SCALE = 2
CELL = 8 * SCALE
SPACE_ADVANCE = 4 * SCALE


class MinecraftFont:
    def __init__(self, fonts_dir=FONTS_DIR):
        self.glyphs = {}
        self.load_definition(fonts_dir)

    def load_definition(self, fonts_dir):
        with open(os.path.join(fonts_dir, "default.json"), encoding="utf-8") as f:
            data = json.load(f)
        for provider in data["providers"]:
            if provider.get("type") != "bitmap":
                continue
            filename = os.path.basename(provider["file"])
            path = os.path.join(fonts_dir, filename)
            if not os.path.exists(path):
                continue
            atlas = Image.open(path).convert("RGBA")
            cols = 16
            cell_w = atlas.width // cols
            cell_h = atlas.height // len(provider["chars"])
            for row, line in enumerate(provider["chars"]):
                for col, char in enumerate(line):
                    if char == "\u0000":
                        continue
                    box = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
                    glyph = atlas.crop(box).resize((CELL, CELL), Image.NEAREST)
                    self.glyphs[ord(char)] = glyph

    def get_glyph(self, code):
        return self.glyphs.get(code)


def parse_motd(text):
    runs = []
    color = (255, 255, 255)
    bold = italic = underline = strike = obf = False
    i = 0
    buf = []
    n = len(text)

    def flush():
        if buf:
            runs.append(
                {
                    "text": "".join(buf),
                    "color": color,
                    "bold": bold,
                    "italic": italic,
                    "underline": underline,
                    "strike": strike,
                    "obf": obf,
                }
            )
            buf.clear()

    while i < n:
        c = text[i]
        if c == "\u00a7" and i + 1 < n:
            code = text[i + 1].lower()
            # Прямой hex-цвет вида §#RRGGBB
            if code == "#" and i + 7 < n and all(ch in "0123456789abcdefABCDEF" for ch in text[i + 2 : i + 8]):
                flush()
                color = tuple(int(text[i + 2 + j : i + 4 + j], 16) for j in (0, 2, 4))
                i += 8
                continue
            if code == "x" and i + 13 < n and all(ch in "0123456789abcdefABCDEF" for ch in text[i + 2 : i + 14]):
                hex6 = text[i + 2 : i + 14][::2]
                flush()
                color = tuple(int(hex6[j : j + 2], 16) for j in (0, 2, 4))
                i += 14
                continue
            if code in CODES:
                flush()
                color = CODES[code]
            elif code in STYLE_CODES:
                flush()
                style = STYLE_CODES[code]
                if style == "bold":
                    bold = True
                elif style == "italic":
                    italic = True
                elif style == "underline":
                    underline = True
                elif style == "strike":
                    strike = True
                elif style == "obf":
                    obf = True
                elif style == "reset":
                    color = (255, 255, 255)
                    bold = italic = underline = strike = obf = False
            i += 2
            continue
        buf.append(c)
        i += 1
    flush()
    return runs


def flatten_chat(component):
    parts = []
    code = ""

    def walk(node):
        nonlocal code
        if isinstance(node, str):
            parts.append(node)
            return
        if not isinstance(node, dict):
            return
        style = ""
        if node.get("bold"):
            style += "\u00a7l"
        if node.get("italic"):
            style += "\u00a7o"
        if node.get("underlined"):
            style += "\u00a7n"
        if node.get("strikethrough"):
            style += "\u00a7m"
        if node.get("obfuscated"):
            style += "\u00a7k"
        color = node.get("color")
        if isinstance(color, str) and color.startswith("#"):
            style += "\u00a7#" + color[1:]
        elif color in CODES:
            style += "\u00a7" + color
        if style:
            parts.append(style)
        text = node.get("text")
        if text is not None:
            parts.append(str(text))
        for extra in node.get("extra", []) or []:
            walk(extra)
        if "translate" in node:
            parts.append(node.get("translate", ""))

    walk(component)
    return "".join(parts)


def render_motd(raw_text, icon_bytes=None, scale=SCALE):
    font = MinecraftFont()
    runs = parse_motd(raw_text)

    icon_img = None
    if icon_bytes:
        try:
            icon_img = Image.open(icon_bytes).convert("RGBA").resize((64 * scale, 64 * scale), Image.NEAREST)
        except Exception:
            icon_img = None

    padding = 24 * scale // 2
    icon_pad = 8 * scale // 2
    panel_w = 340 * scale
    text_left = padding + (icon_img.width + icon_pad if icon_img else 0)
    text_right = panel_w - padding
    text_width = text_right - text_left

    lines = []
    cur_line = []
    cur_w = 0
    for run in runs:
        words = run["text"].split(" ")
        for w_idx, word in enumerate(words):
            word_w = sum(SPACE_ADVANCE if ord(ch) == 32 else CELL for ch in word)
            sep = " " if (w_idx or cur_line) else ""
            sep_w = SPACE_ADVANCE if sep else 0
            if cur_line and cur_w + sep_w + word_w > text_width:
                lines.append(cur_line)
                cur_line = []
                cur_w = 0
                sep = ""
                sep_w = 0
            cur_line.append((sep, run))
            cur_line.append((word, run))
            cur_w += sep_w + word_w
    if cur_line:
        lines.append(cur_line)

    line_h = CELL + 2 * scale
    text_h = len(lines) * line_h
    content_h = max(text_h, icon_img.height if icon_img else 0)
    panel_h = padding * 2 + content_h

    img = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 0))
    overlay = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((0, 0, panel_w - 1, panel_h - 1), radius=4 * scale, fill=(0, 0, 0, 160))
    od.rounded_rectangle((0, 0, panel_w - 1, panel_h - 1), radius=4 * scale, outline=(255, 255, 255, 70), width=scale)
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)
    if icon_img:
        img.paste(icon_img, (padding, padding), icon_img)

    y = padding
    for line in lines:
        x = text_left
        for seg_text, run in line:
            if not seg_text:
                continue
            seg_glyphs = []
            for ch in seg_text:
                code = ord(ch)
                if code == 32:
                    seg_glyphs.append(None)
                    continue
                glyph = font.get_glyph(code)
                if glyph is None:
                    glyph = font.get_glyph(ord("?"))
                if run["obf"] and glyph is not None:
                    seg_glyphs.append(glyph)
                    continue
                seg_glyphs.append(glyph)
            for glyph in seg_glyphs:
                if glyph is None:
                    x += SPACE_ADVANCE
                    continue
                if run["italic"]:
                    glyph = glyph.transform(
                        glyph.size,
                        Image.AFFINE,
                        (1, -0.2, 0, 0, 1, 0),
                        resample=Image.NEAREST,
                    )
                tinted = glyph.copy()
                mask = tinted.split()[3].point(lambda a: a > 60 and 255)
                solid = Image.new("RGBA", tinted.size, run["color"] + (255,))
                tinted = Image.composite(solid, Image.new("RGBA", tinted.size, (0, 0, 0, 0)), mask)
                img.paste(tinted, (x, y), tinted)
                if run["bold"]:
                    img.paste(tinted, (x + scale, y), tinted)
                if run["underline"] or run["strike"]:
                    line_y = y + (CELL + 2 * scale if run["underline"] else CELL // 2)
                    draw.line((x, line_y, x + CELL - 1, line_y), fill=run["color"], width=scale)
                x += CELL
        y += line_h

    return img