"""Finds titles with characters no installed font can draw, and names the Arch package that fixes it."""
import unicodedata

# (words in the Unicode character name, package)
SCRIPTS = (
    (("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "BOPOMOFO", "IDEOGRAPHIC"), "noto-fonts-cjk"),
    (("EMOJI",), "noto-fonts-emoji"),
    (("ARABIC", "HEBREW", "THAI", "DEVANAGARI", "BENGALI", "TAMIL", "ETHIOPIC", "GEORGIAN", "ARMENIAN",
      "KHMER", "LAO", "MYANMAR", "SINHALA", "TIBETAN", "MATHEMATICAL"), "noto-fonts-extra"),
)


def package_for(char):
    if ord(char) >= 0x1F300:
        return "noto-fonts-emoji"
    name = unicodedata.name(char, "")
    for words, package in SCRIPTS:
        if any(word in name for word in words):
            return package
    return "noto-fonts"


def missing_packages(texts, widget):
    """Packages that would draw the characters Pango cannot, for the given texts (needs a widget)."""
    packages = {}
    for text in texts:
        if not text or text.isascii():
            continue
        layout = widget.create_pango_layout(text)
        if layout.get_unknown_glyphs_count() == 0:
            continue
        for char in set(text):
            if ord(char) > 127:
                single = widget.create_pango_layout(char)
                if single.get_unknown_glyphs_count():
                    packages.setdefault(package_for(char), text)
    return packages
