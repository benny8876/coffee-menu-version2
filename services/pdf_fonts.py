"""Myanmar font and bilingual text helpers for PDF exports."""

import re
from pathlib import Path
from typing import Optional, Tuple
from xml.sax.saxutils import escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph

import schemas

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_MYANMAR = "NotoSansMyanmar"
FONT_LATIN = "Helvetica"

MYANMAR_RE = re.compile(r"[\u1000-\u109F]")
LATIN_RE = re.compile(r"[A-Za-z]")

CUSTOM_CATEGORY_ENGLISH = {
    "ကြက်ဥ": "Eggs",
    "ဆီ": "Oil",
    "အာလူးသီး": "Potato",
    "အလှူဒါန": "Donation",
}

_registered = False


def register_myanmar_font() -> None:
    global _registered
    if _registered:
        return
    font_path = FONTS_DIR / "NotoSansMyanmar-Regular.ttf"
    if not font_path.exists():
        raise FileNotFoundError(
            f"Myanmar font missing: {font_path}. "
            "Add NotoSansMyanmar-Regular.ttf to assets/fonts/."
        )
    pdfmetrics.registerFont(TTFont(FONT_MYANMAR, str(font_path)))
    _registered = True


def has_myanmar(text: str) -> bool:
    return bool(MYANMAR_RE.search(text or ""))


def has_latin(text: str) -> bool:
    return bool(LATIN_RE.search(text or ""))


def resolve_expense_category_labels(category: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (english, myanmar) for an expense category name."""
    name = (category or "").strip()
    if not name:
        return None, None

    if name in schemas.EXPENSE_CATEGORY_MYANMAR:
        return name, schemas.EXPENSE_CATEGORY_MYANMAR[name]

    if name in CUSTOM_CATEGORY_ENGLISH:
        return CUSTOM_CATEGORY_ENGLISH[name], name

    reverse = {v: k for k, v in schemas.EXPENSE_CATEGORY_MYANMAR.items()}
    if name in reverse:
        return reverse[name], name

    if has_myanmar(name):
        return None, name

    return name, None


def _cell_style(font_name: str, font_size: int = 9) -> ParagraphStyle:
    return ParagraphStyle(
        "PdfCell",
        fontName=font_name,
        fontSize=font_size,
        leading=font_size + 4,
    )


def bilingual_category_paragraph(category: str, font_size: int = 9) -> Paragraph:
    """English + Myanmar on separate lines for expense categories."""
    register_myanmar_font()
    english, myanmar = resolve_expense_category_labels(category)
    parts = []

    if english:
        parts.append(f'<font name="{FONT_LATIN}">{escape(english)}</font>')
    if myanmar:
        if english:
            parts.append("<br/>")
        parts.append(f'<font name="{FONT_MYANMAR}">{escape(myanmar)}</font>')

    if not parts:
        return Paragraph("—", _cell_style(FONT_LATIN, font_size))

    return Paragraph("".join(parts), _cell_style(FONT_LATIN, font_size))


def mixed_text_paragraph(text: str, font_size: int = 9) -> Paragraph:
    """Render free text with the right font(s) for Latin and/or Myanmar."""
    register_myanmar_font()
    raw = (text or "").strip() or "—"

    if raw == "—":
        return Paragraph("—", _cell_style(FONT_LATIN, font_size))

    if has_myanmar(raw) and has_latin(raw):
        parts = []
        latin_text = MYANMAR_RE.sub(" ", raw)
        latin_text = re.sub(r"\s+", " ", latin_text).strip()
        myanmar_text = " ".join(MYANMAR_RE.findall(raw))
        if latin_text:
            parts.append(f'<font name="{FONT_LATIN}">{escape(latin_text)}</font>')
        if myanmar_text:
            if parts:
                parts.append("<br/>")
            parts.append(f'<font name="{FONT_MYANMAR}">{escape(myanmar_text)}</font>')
        return Paragraph("".join(parts) or "—", _cell_style(FONT_LATIN, font_size))

    if has_myanmar(raw):
        return Paragraph(escape(raw), _cell_style(FONT_MYANMAR, font_size))

    return Paragraph(escape(raw), _cell_style(FONT_LATIN, font_size))
