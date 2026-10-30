"""Markdown -> Telegram HTML and light LaTeX post-processing for replies.

Telegram Bot API understands a small HTML subset:
b, i, u, s, spoiler, quote, code, pre, a, tg-spoiler.

This module converts the Markdown that language models usually emit into that
subset and prettifies simple LaTeX that models leave as plain text.
The implementation is intentionally regex/scan based (no external deps).
"""

from __future__ import annotations

import re
from html import escape as _html_escape

# --------------------------------------------------------------------------
# Unicode superscripts / subscripts
# --------------------------------------------------------------------------

_SUPER_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
    "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ",
    "g": "ᵍ", "h": "ʰ", "i": "ᶦ", "j": "ʲ", "k": "ᵏ", "l": "ˡ",
    "m": "ᵐ", "n": "ⁿ", "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ",
    "t": "ᵗ", "u": "ᵘ", "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ",
    "z": "ᶻ",
}

_SUB_MAP = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅",
    "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}


def _convert_map(text: str, table: dict[str, str]) -> str | None:
    """Return converted text, or None when some character has no mapping."""
    out = []
    for ch in text:
        if ch not in table:
            return None
        out.append(table[ch])
    return "".join(out)


# --------------------------------------------------------------------------
# LaTeX symbol tables
# --------------------------------------------------------------------------

_SYMBOLS: dict[str, str] = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "ϕ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "cdot": "·", "ast": "∗", "star": "⋆", "circ": "∘",
    "bullet": "∙", "otimes": "⊗", "oplus": "⊕", "odot": "⊙",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "equiv": "≡", "approx": "≈",
    "cong": "≅", "sim": "∼", "simeq": "≃", "propto": "∝",
    "ll": "≪", "gg": "≫", "doteq": "≐",
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀",
    "exists": "∃", "neg": "¬", "lnot": "¬", "land": "∧",
    "wedge": "∧", "lor": "∨", "vee": "∨", "emptyset": "∅",
    "varnothing": "∅", "in": "∈", "notin": "∉", "ni": "∋",
    "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
    "supseteq": "⊇", "cup": "∪", "cap": "∩", "setminus": "∖",
    "to": "→", "rightarrow": "→", "longrightarrow": "⟶",
    "leftarrow": "←", "longleftarrow": "⟵",
    "leftrightarrow": "↔", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "Leftrightarrow": "⇔", "mapsto": "↦", "implies": "⟹",
    "iff": "⟺", "uparrow": "↑", "downarrow": "↓",
    "prime": "′", "degree": "°", "angle": "∠", "perp": "⊥",
    "parallel": "∥", "mid": "∣", "nmid": "∤", "therefore": "∴",
    "because": "∵", "ldots": "…", "cdots": "⋯", "vdots": "⋮",
    "ddots": "⋱", "dots": "…", "hellip": "…",
    "langle": "⟨", "rangle": "⟩", "lceil": "⌈", "rceil": "⌉",
    "lfloor": "⌊", "rfloor": "⌋", "S": "§",
    "star_": "★",  # unused placeholder, kept harmless
}

# Rendered as "f(x)" style wrappers: command{arg}
_UNARY: dict[str, str] = {
    "sqrt": lambda a: f"√{a if _is_single(a) else '(' + a + ')'}",
    "text": lambda a: a,
    "mathrm": lambda a: a,
    "mathbf": lambda a: f"<b>{a}</b>",
    "mathit": lambda a: a,
    "operatorname": lambda a: a,
    "left": lambda a: a,
    "right": lambda a: a,
    "big": lambda a: a,
    "Big": lambda a: a,
    "bigg": lambda a: a,
    "lvert": lambda a: f"|{a}|",
    "overline": lambda a: f"{a}‾",
    "hat": lambda a: f"{a}̂",
    "vec": lambda a: f"{a}⃗",
    "bar": lambda a: f"{a}‾",
    "dot": lambda a: f"{a}̇",
}

_BINARY: dict[str, str] = {
    "frac": lambda a, b: f"{a}/{b}" if _is_single(a) and _is_single(b) else f"({a})/({b})",
    "dfrac": lambda a, b: _BINARY["frac"](a, b),
    "tfrac": lambda a, b: _BINARY["frac"](a, b),
    "binom": lambda a, b: f"C({a}, {b})",
    "dbinom": lambda a, b: f"C({a}, {b})",
}

# Commands that we deliberately leave untouched (they are not math here,
# e.g. \textbf inside a markdown fragment, or \in inside prose).
_IGNORED = {"ni", "mid", "parallel"}

_MATH_MARKERS = re.compile(
    r"\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|lim|left|right|mathbf|mathrm"
    r"|binom|cdot|times|leq|geq|neq|approx|infty|alpha|beta|gamma|delta"
    r"|theta|lambda|mu|pi|sigma|phi|omega|pi)\b"
)


def _is_single(text: str) -> bool:
    """True when `text` is a single token that needs no parentheses."""
    return bool(re.fullmatch(r"[A-Za-z0-9]+|[-+0-9.]+", text))


# --------------------------------------------------------------------------
# Brace-aware helpers
# --------------------------------------------------------------------------

def _matching_brace(text: str, start: int) -> int:
    """Index of the '}' matching the '{' at `start`, or -1."""
    if start >= len(text) or text[start] != "{":
        return -1
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _read_group(text: str, pos: int) -> tuple[str, int] | None:
    """Read ``{...}`` or a single escaped command/character at `pos`.

    Returns (content, index_after_group) or None when nothing readable.
    """
    if pos >= len(text):
        return None
    if text[pos] == "{":
        end = _matching_brace(text, pos)
        if end == -1:
            return None
        return text[pos + 1:end], end + 1
    m = re.match(r"\\[A-Za-z]+|\\.|.", text[pos:])
    if not m:
        return None
    return m.group(0), pos + len(m.group(0))


def _apply_binary(text: str, name: str, render) -> str:
    out: list[str] = []
    i = 0
    token = "\\" + name
    while True:
        j = text.find(token, i)
        if j == -1:
            out.append(text[i:])
            break
        k = j + len(token)
        first = _read_group(text, k)
        if not first:
            out.append(text[i:k])
            i = k
            continue
        second = _read_group(text, first[1])
        if not second:
            out.append(text[i:k])
            i = k
            continue
        out.append(text[i:j])
        out.append(render(_render_math_body(first[0]), _render_math_body(second[0])))
        i = second[1]
    return "".join(out)


def _apply_unary(text: str, name: str, render) -> str:
    out: list[str] = []
    i = 0
    token = "\\" + name
    while True:
        j = text.find(token, i)
        if j == -1:
            out.append(text[i:])
            break
        k = j + len(token)
        group = _read_group(text, k)
        if not group:
            out.append(text[i:k])
            i = k
            continue
        out.append(text[i:j])
        out.append(render(_render_math_body(group[0])))
        i = group[1]
    return "".join(out)


def _apply_scripts(text: str, allow_bare_sub: bool = True) -> str:
    """Prettify ^ and _ (braced groups and simple tokens).

    When allow_bare_sub=False (prose mode), skip bare ``_digit`` conversions
    to avoid mangling snake_case identifiers.
    """
    for sigil, table, tag in (("^", _SUPER_MAP, "sup"), ("_", _SUB_MAP, "sub")):
        out: list[str] = []
        i = 0
        while True:
            j = text.find(sigil, i)
            if j == -1:
                out.append(text[i:])
                break
            # Skip things like a**b, snake_case identifiers, URLs.
            prev = text[j - 1] if j else ""
            nxt = text[j + 1] if j + 1 < len(text) else ""
            bad_prev = (prev == sigil) or (
                sigil == "_"
                and (prev.isalnum() or prev == "_")
                and not allow_bare_sub
            )
            if bad_prev or not nxt or nxt in " \t\n" + sigil:
                out.append(text[i:j + 1])
                i = j + 1
                continue
            group = _read_group(text, j + 1)
            if not group or not group[0] or len(group[0]) > 12:
                out.append(text[i:j + 1])
                i = j + 1
                continue
            converted = _convert_map(group[0], table)
            out.append(text[i:j])
            out.append(converted if converted is not None else f"<{tag}>{group[0]}</{tag}>")
            i = group[1]
        text = "".join(out)
    return text


def _render_math_body(text: str, allow_bare_sub: bool = True) -> str:
    """Process LaTeX commands/symbols/scripts. Text is already escaped."""
    for name, render in _BINARY.items():
        text = _apply_binary(text, name, render)
    for name, render in _UNARY.items():
        text = _apply_unary(text, name, render)

    text = re.sub(r"\\(?:text|mathrm|mathit|operatorname)\s*\{([^{}]*)\}", r"\1", text)

    def _sym(match: re.Match) -> str:
        name = match.group(1)
        if name in _IGNORED:
            return match.group(0)
        return _SYMBOLS.get(name, match.group(0))

    text = re.sub(r"\\([A-Za-z]+)", _sym, text)
    text = _apply_scripts(text, allow_bare_sub=allow_bare_sub)
    return text


def _render_math(content: str) -> str:
    """Render a LaTeX fragment into pretty unicode + Telegram HTML."""
    return _render_math_body(_html_escape(content, quote=False))


def render_math(content: str) -> str:
    """Public helper: render one LaTeX fragment (no delimiters)."""
    return _render_math(content)




def _render_bare_latex(text: str) -> str:
    """Render LaTeX commands in prose text (already HTML-escaped).

    Processes: frac, sqrt, alpha, in, ^2, _{...}.
    Skips bare _digit (snake_case safety) unless explicitly braced.
    """
    # Binary: frac{a}{b}, dfrac, tfrac, binom
    for name, render in _BINARY.items():
        text = _apply_binary(text, name, render)
    # Unary: \sqrt, \text, \mathrm, etc.
    for name, render in _UNARY.items():
        text = _apply_unary(text, name, render)
    # Text wrappers
    text = re.sub(
        r"\\(?:text|mathrm|mathit|operatorname)\s*\{([^{}]*)\}", r"\1", text
    )
    # Greek + symbols (skip \in, \ni etc. — not meaningful in prose)
    def _sym(match: re.Match) -> str:
        name = match.group(1)
        if name in _IGNORED:
            return match.group(0)
        return _SYMBOLS.get(name, match.group(0))
    text = re.sub(r"\\([A-Za-z]+)", _sym, text)
    # Scripts: ^{...}, _{...}, ^c, ^digit, _{digit}
    text = _apply_scripts(text, allow_bare_sub=False)
    # Clean up empty tags
    text = re.sub(r"<sup>\s*</sup>", "", text)
    text = re.sub(r"<sub>\s*</sub>", "", text)
    return text

# --------------------------------------------------------------------------
# Placeholder store
# --------------------------------------------------------------------------

class _Store:
    """Collects pre-rendered HTML fragments behind unique \\\\x00 markers."""

    _KINDS = ("CODE", "MATH")

    def __init__(self) -> None:
        self._items: dict[str, str] = {}
        self._counters = {kind: 0 for kind in self._KINDS}

    def put(self, kind: str, html: str) -> str:
        index = self._counters[kind]
        self._counters[kind] = index + 1
        key = f"\x00{kind}{index}\x00"
        self._items[key] = html
        return key

    def restore(self, text: str) -> str:
        # Longest keys first so \x00CODE1\x00 never clobbers \x00CODE11\x00.
        for key in sorted(self._items, key=len, reverse=True):
            text = text.replace(key, self._items[key])
        return text


# --------------------------------------------------------------------------
# Protection passes (run before escaping)
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^([ \t]*)```[^\n]*\n(.*?)\n[ \t]*```[ \t]*$", re.M | re.S)
_FENCE_OPEN_RE = re.compile(r"^([ \t]*)```[^\n]*\n(.*)$", re.S)
_INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")


def _protect_code(text: str, store: _Store) -> str:
    def fence(match: re.Match) -> str:
        body = match.group(2).rstrip("\n")
        return "\n" + store.put("CODE", f"<pre><code>{_html_escape(body, quote=False)}</code></pre>") + "\n"

    text = _FENCE_RE.sub(fence, text)
    text = _FENCE_OPEN_RE.sub(fence, text)  # unterminated fence -> render what we have

    def inline(match: re.Match) -> str:
        return store.put("CODE", f"<code>{_html_escape(match.group(1), quote=False)}</code>")

    return _INLINE_CODE_RE.sub(inline, text)


_MATH_INLINE_RE = re.compile(r"\$(?!\s)([^$\n]{1,300}?)(?<!\s)\$")
_MATH_BLOCK_RE = re.compile(r"\$\$([^$]{1,2000}?)\$\$", re.S)
_MATH_PAREN_RE = re.compile(r"\\\((.+?)\\\)", re.S)
_MATH_BRACKET_RE = re.compile(r"\\\[(.+?)\\\]", re.S)

# A "$...$" pair is only treated as math when it contains a math signal,
# otherwise prices like "$5 and $10" would be mangled.
_MATH_LIKE_RE = re.compile(
    r"[\^_]|\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|lim|left|right|mathbf|mathrm|binom|cdot|times|leq|geq|neq|approx|infty|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega|in|subseteq|supseteq|subset|supset|cup|cap|forall|exists|equiv|propto|cong|sim|doteq)"
)
def _protect_math(text: str, store: _Store) -> str:
    def block(match: re.Match) -> str:
        return store.put("MATH", _render_math(match.group(1).strip()))

    def inline(match: re.Match) -> str:
        body = match.group(1)
        if not _MATH_LIKE_RE.search(body):
            return match.group(0)
        return store.put("MATH", _render_math(body))

    text = _MATH_BLOCK_RE.sub(block, text)
    text = _MATH_PAREN_RE.sub(block, text)
    text = _MATH_BRACKET_RE.sub(block, text)
    return _MATH_INLINE_RE.sub(inline, text)


# --------------------------------------------------------------------------
# Structural pass (runs after escaping)
# --------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_QUOTE_RE = re.compile(r"^[ \t]*(?:&gt;|>)[ \t]?(.*)$")
_UL_RE = re.compile(r"^([ \t]*)[*+\u2022\u25cf\u25cb-]\s+(.*)$")
_OL_RE = re.compile(r"^([ \t]*)(\d{1,9})[.)]\s+(.*)$")


def _indent_level(spaces: str) -> int:
    return min(len(spaces.expandtabs(4)) // 2, 3)


def _structure_lines(text: str) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        if _HR_RE.match(line):
            out.append("──────────")
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            title = heading.group(2).strip()
            out.append(f"<b>{title}</b>" if title else "")
            continue

        quote = _QUOTE_RE.match(line)
        if quote:
            body = quote.group(1).strip()
            out.append(f"<blockquote>{body}</blockquote>" if body else "")
            continue

        bullet = _UL_RE.match(line)
        if bullet:
            body = bullet.group(2).rstrip()
            if not body:
                continue
            out.append("　" * _indent_level(bullet.group(1)) + _pick_bullet(body) + " " + body)
            continue

        numbered = _OL_RE.match(line)
        if numbered:
            body = numbered.group(3).rstrip()
            if not body:
                continue
            out.append(
                "　" * _indent_level(numbered.group(1))
                + f"{numbered.group(2)}) {body}"
            )
            continue

        out.append(line)
    return "\n".join(out)


def _pick_bullet(body: str) -> str:
    """Avoid the '— —' clash when the item itself starts with a dash."""
    return "◦" if body[:1] in "-–—" else "•"


# --------------------------------------------------------------------------
# Tables (only for real tables: >= 2 rows plus a separator row)
# --------------------------------------------------------------------------

_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _split_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    return [cell.strip() for cell in line.split("|")]


def _find_tables(text: str, store: _Store) -> str:
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        if (
            i + 1 < len(lines)
            and "|" in lines[i]
            and _TABLE_SEP_RE.match(lines[i + 1])
        ):
            header = _split_row(lines[i])
            j = i + 2
            rows: list[list[str]] = []
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(_split_row(lines[j]))
                j += 1
            result.append(store.put("CODE", _render_table(header, rows)))
            i = j
            continue
        result.append(lines[i])
        i += 1
    return "\n".join(result)


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    widths = [len(c) for c in header]
    for row in rows:
        for idx, cell in enumerate(row):
            if idx < len(widths):
                widths[idx] = max(widths[idx], len(cell))
            else:
                widths.append(len(cell))

    def fmt(cells: list[str]) -> str:
        parts = []
        for idx, cell in enumerate(cells):
            width = widths[idx] if idx < len(widths) else len(cell)
            parts.append(cell.ljust(width))
        return " │ ".join(parts).rstrip()

    out = [fmt(header), "─" * max(sum(widths) + 3 * (len(widths) - 1), 8)]
    out.extend(fmt(row) for row in rows)
    return _html_escape("\n".join(out), quote=False)


# --------------------------------------------------------------------------
# Inline styles (run after escaping, so raw '<' never becomes a tag)
# --------------------------------------------------------------------------

_CODEX_RE = re.compile(r"``([^`\n]{1,200})``")
_BOLD_STAR_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_BOLD_UNDER_RE = re.compile(r"__(?=\S)(.+?)(?<=\S)__", re.S)
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", re.S)
_ITALIC_UNDER_RE = re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", re.S)
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S)
_SPOILER_RE = re.compile(r"\|\|(.+?)\|\|", re.S)
_UNDERSCORE_TAG_RE = re.compile(r"<u>(.*?)</u>", re.S)


def _inline_styles(text: str) -> str:
    # Telegram has no <u>; code fences used as ``code`` map to inline code.
    text = _CODEX_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_STAR_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_UNDER_RE.sub(r"<b>\1</b>", text)
    text = _SPOILER_RE.sub(r"<spoiler>\1</spoiler>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _ITALIC_STAR_RE.sub(r"<i>\1</i>", text)
    # Markdown '_' italics collide with snake_case, keep only tight words.
    text = _ITALIC_UNDER_RE.sub(r"<i>\1</i>", text)
    return text


_LINK_MD_RE = re.compile(r"(?<!!)\[([^\]\n]{1,300})\]\(([^)\s]{1,500})\)")
_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]{1,500})>")
_BARE_URL_RE = re.compile(r"(?<![\w/>=\"'])(https?://[^\s<\"]{1,500})")


def _links(text: str, store: _Store) -> str:
    def anchor(label: str, href: str) -> str:
        href = href.replace(" ", "%20")
        return store.put("CODE", f'<a href="{_html_escape(href, quote=True)}">{label}</a>')

    text = _LINK_MD_RE.sub(lambda m: anchor(m.group(1), m.group(2)), text)
    text = _AUTOLINK_RE.sub(lambda m: anchor(m.group(1), m.group(1)), text)
    text = _BARE_URL_RE.sub(lambda m: anchor(m.group(1), m.group(1)), text)
    return text


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def to_telegram_html(text: str) -> str:
    """Convert a Markdown/LaTeX answer into Telegram-safe HTML."""
    if not text:
        return ""

    # Strip stray NUL bytes so they cannot collide with our placeholders.
    store = _Store()
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text = _protect_code(text, store)
    text = _protect_math(text, store)

    text = _html_escape(text, quote=False)

    # Render bare LaTeX (no $...$ delimiters) — safe after escaping.
    text = _render_bare_latex(text)

    text = _find_tables(text, store)
    text = _structure_lines(text)
    text = _inline_styles(text)
    text = _links(text, store)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return store.restore(text).strip()


def strip_markdown(text: str) -> str:
    """Plain-text fallback: readable text with markdown markers removed."""
    if not text:
        return ""
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.S)
    text = re.sub(r"```[^\n]*\n?", "", text)
    text = re.sub(r"`([^`\n]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_~]{1,3}(.+?)[*_~]{1,3}", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    text = re.sub(r"\$\$([^$]*)\$\$", r"\1", text)
    text = re.sub(r"\$([^$\n]+)\$", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_html_error(error_text: str) -> bool:
    """True when Telegram rejected a message because of bad entities."""
    lowered = (error_text or "").lower()
    return "can't parse entities" in lowered or "unterminated" in lowered
