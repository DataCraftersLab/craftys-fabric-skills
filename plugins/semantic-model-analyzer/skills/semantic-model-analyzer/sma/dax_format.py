"""
A TMDL format string, as a modeller reads it in Power BI Desktop.

TMDL stores a VBA mask and nothing else. What anyone actually sets is the **Format**
dropdown and the ribbon toggles beside it, and the mask is what those two produce. So
the mask is decoded back into that pair wherever it is shown, because the pair is the
thing a reader recognises.

Its own file because two consumers need it: the showcase renders it as a column, and
`tmdl_findings.py` uses the dropdown word to tell a real format inconsistency (two
spellings of a percentage inside one folder) from a normal one (a currency measure and
a percentage measure inside one folder).
"""

import re

BS = chr(92)

# A named format is a word Power BI resolves itself; the mask is never shown. The second
# value is the toggle state it implies, since a named format still lights the ribbon.
NAMED_FORMAT = {
    "general number": ("General", "", None), "general": ("General", "", None),
    "standard": ("Decimal number", ",", 2), "fixed": ("Decimal number", "", 2),
    "currency": ("Currency", "$,", 2), "percent": ("Percentage", "%", 2),
    "scientific": ("Scientific", "", 2),
    "general date": ("Date/time", "", None), "long date": ("Date/time", "", None),
    "medium date": ("Date/time", "", None), "short date": ("Date/time", "", None),
    "long time": ("Date/time", "", None), "medium time": ("Date/time", "", None),
    "short time": ("Date/time", "", None),
    "yes/no": ("True/false", "", None), "true/false": ("True/false", "", None),
    "on/off": ("True/false", "", None),
}

CURRENCY_CHARS = "$£€¥₹₩₪₽₿"
DATE_TOKEN_RE = re.compile(r"(?<![a-z])(yyyy|yy|mmmm|mmm|mm|dddd|ddd|hh|nn|ss|am/pm|a/p)(?![a-z])")
# A backslash escapes the next character in a VBA mask, so `\$` is a literal dollar and
# not part of the numeric pattern. Built from chr(92) rather than written as an escape:
# a literal double backslash in this source has been collapsed by a shell heredoc before,
# which silently turned "strip escaped literals" into "strip every decimal point".
ESCAPED_RE = re.compile(BS + BS + ".")
QUOTED_RE = re.compile(r'""[^"]*""|"[^"]*"')


def read_format(fs):
    """A TMDL format string as a modeller reads it in Power BI Desktop.

    The mask is not what anyone sets. What they set is the **Format** dropdown and the
    ribbon toggles beside it, and the mask is what those two produce. So the mask is
    decoded back into the pair, and both are shown -- the word for what it is, the
    toggles for how it got that way.

    word     the Format dropdown reads this
    flags    the toggles that would be lit: $ % thousands, and +- for an explicit
             negative section, which the ribbon has no button for but the mask carries
    dp       decimal places the mask fixes, or None for Auto
    sections mask sections: positive, negative, zero
    """
    raw = (fs or "").strip()
    if not raw:
        return {"word": "General", "flags": [], "dp": None, "sections": 1, "named": False}

    named = NAMED_FORMAT.get(raw.lower())
    if named:
        word, toggles, dp = named
        return {"word": word, "flags": [c for c in "$%," if c in toggles],
                "dp": dp, "sections": 1, "named": True}

    # Sections split on `;`, but a quoted literal may hold one, so blank the quoted runs
    # first. NUL is safe as the placeholder: the parser would never emit one.
    bare = QUOTED_RE.sub("\x00", raw)
    parts = bare.split(";")
    sections = len(parts)
    head = parts[0]

    # A mask whose positive section is nothing but a quoted literal is a boolean display.
    if sections >= 2 and re.fullmatch(r"[\x00\s]*", head):
        return {"word": "True/false", "flags": [], "dp": None,
                "sections": sections, "named": False}

    flags = []
    if "%" in head:
        flags.append("%")
    if re.search("[" + CURRENCY_CHARS + "]", raw):
        flags.append("$")
    if re.search("#,|,0|,#", head):
        flags.append(",")
    if sections > 1:
        flags.append("±")

    mask = ESCAPED_RE.sub("", head).replace("\x00", "")
    scientific = bool(re.search("[eE][+-]", mask))
    # In scientific notation the digits after the exponent are the exponent's own width,
    # not decimal places, so the mantissa is what carries dp.
    mantissa = re.split("[eE]", mask)[0] if scientific else mask
    dp = len(re.findall("[0#]", mantissa.split(".", 1)[1])) if "." in mantissa else 0

    if scientific:
        word = "Scientific"
    elif "%" in flags:
        word = "Percentage"
    elif "$" in flags:
        word = "Currency"
    elif DATE_TOKEN_RE.search(mask.lower()) or re.search("(?<![a-z0#,.])d{1,4}(?![a-z0#])",
                                                        mask.lower()):
        word, dp = "Date/time", None
    elif not re.search("[0#]", mask):
        word, dp = "Text", None
    elif dp:
        word = "Decimal number"
    else:
        word = "Whole number"
    return {"word": word, "flags": flags, "dp": dp, "sections": sections, "named": False}


FORMAT_TONE = {
    "General": "var(--a0-dot)", "Currency": "var(--a2-dot)", "Percentage": "var(--a1-dot)",
    "Whole number": "var(--a3-dot)", "Decimal number": "var(--a4-dot)",
    "Scientific": "var(--a5-dot)", "Date/time": "var(--a6-dot)",
    "True/false": "var(--a7-dot)", "Text": "var(--a8-dot)",
}
