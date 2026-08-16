"""html.parser: HTML parser (accessed as html_parser).

Pure Python, incremental HTML tokenizer. Subclass HTMLParser and override
handle_* methods to process HTML events.

Usage::

    import html_parser

    class MyParser(html_parser.HTMLParser):
        def handle_starttag(self, tag, attrs):
            print("Start:", tag, attrs)
        def handle_endtag(self, tag):
            print("End:", tag)
        def handle_data(self, data):
            print("Data:", repr(data))

    p = MyParser()
    p.feed("<html><body><p>Hello &amp; World</p></body></html>")
    p.close()
"""
from __future__ import annotations


class HTMLParseError(Exception):
    """Deprecated: HTML parse error."""

    def __init__(self, msg: str = "", position: list = [0, 0]) -> None:
        self.msg: str = msg
        self.lineno: int = position[0]
        self.offset: int = position[1]

    def __str__(self) -> str:
        return "HTMLParseError: " + self.msg


class HTMLParser:
    """Push-based HTML parser.

    Feed data incrementally with feed(); override handle_* methods to
    receive events.
    """

    CDATA_CONTENT_ELEMENTS: list = ["script", "style"]

    def __init__(self, convert_charrefs: int = 1) -> None:
        self.convert_charrefs: int = convert_charrefs
        self._buf: str = ""
        self._lineno: int = 1
        self._offset: int = 0

    def reset(self) -> None:
        """Reset the parser."""
        self._buf = ""
        self._lineno = 1
        self._offset = 0

    def feed(self, data: str) -> None:
        """Feed data to the parser."""
        self._buf = self._buf + data
        self._process()

    def close(self) -> None:
        """Force processing of any remaining data."""
        if self._buf:
            self.handle_data(self._buf)
            self._buf = ""

    def getpos(self) -> list:
        """Return [line, col] of the last call to the handle_* method."""
        return [self._lineno, self._offset]

    def get_starttag_text(self) -> str:
        """Return the text of the last opening tag."""
        return ""

    # ------------------------------------------------------------------
    # Internal processing
    # ------------------------------------------------------------------

    def _process(self) -> None:
        s: str = self._buf
        i: int = 0
        n: int = len(s)
        text_start: int = 0
        while i < n:
            if s[i] != "<":
                i = i + 1
                continue
            # Flush preceding text
            if i > text_start:
                text: str = s[text_start:i]
                if self.convert_charrefs:
                    text = unescape(text)
                self.handle_data(text)
            # What follows '<'?
            if i + 1 >= n:
                # Incomplete tag — wait for more data
                self._buf = s[i:]
                return
            c: str = s[i + 1]
            if c == "!":
                result: list = self._handle_decl_or_comment(s, i)
                i = result[0]
                text_start = i
            elif c == "/":
                result2: list = self._handle_endtag(s, i)
                i = result2[0]
                text_start = i
            elif c == "?":
                result3: list = self._handle_pi(s, i)
                i = result3[0]
                text_start = i
            elif c.isalpha() or c == "_":
                result4: list = self._handle_starttag(s, i)
                i = result4[0]
                text_start = i
            else:
                # Not a real tag
                self.handle_data("<")
                i = i + 1
                text_start = i
        # Remaining text
        if text_start < n:
            text = s[text_start:]
            if self.convert_charrefs:
                text = unescape(text)
            self.handle_data(text)
        self._buf = ""

    def _handle_starttag(self, s: str, i: int) -> list:
        """Parse a start tag starting at s[i] ('<'). Returns [next_i]."""
        n: int = len(s)
        i = i + 1  # skip '<'
        # Read tag name
        start: int = i
        while i < n and s[i] not in " \t\r\n/>":
            i = i + 1
        tag: str = s[start:i].lower()
        # Parse attributes
        attrs: list = []
        while i < n:
            # Skip whitespace
            while i < n and s[i] in " \t\r\n":
                i = i + 1
            if i >= n:
                break
            if s[i] == ">":
                i = i + 1
                self.handle_starttag(tag, attrs)
                return [i]
            if s[i] == "/" and i + 1 < n and s[i + 1] == ">":
                i = i + 2
                self.handle_startendtag(tag, attrs)
                return [i]
            # Attribute name
            astart: int = i
            while i < n and s[i] not in " \t\r\n=/>":
                i = i + 1
            attr_name: str = s[astart:i].lower()
            # Skip whitespace
            while i < n and s[i] in " \t\r\n":
                i = i + 1
            if i < n and s[i] == "=":
                i = i + 1
                while i < n and s[i] in " \t\r\n":
                    i = i + 1
                if i < n and s[i] in ('"', "'"):
                    quote: str = s[i]
                    i = i + 1
                    vstart: int = i
                    while i < n and s[i] != quote:
                        i = i + 1
                    attr_val: str = s[vstart:i]
                    if i < n:
                        i = i + 1
                else:
                    vstart = i
                    while i < n and s[i] not in " \t\r\n>":
                        i = i + 1
                    attr_val = s[vstart:i]
                if self.convert_charrefs:
                    attr_val = unescape(attr_val)
                attrs.append([attr_name, attr_val])
            else:
                attrs.append([attr_name, attr_name])
        self.handle_starttag(tag, attrs)
        return [i]

    def _handle_endtag(self, s: str, i: int) -> list:
        """Parse an end tag '</tag>'."""
        n: int = len(s)
        i = i + 2  # skip '</'
        start: int = i
        while i < n and s[i] not in " \t\r\n>":
            i = i + 1
        tag: str = s[start:i].lower()
        while i < n and s[i] != ">":
            i = i + 1
        if i < n:
            i = i + 1
        self.handle_endtag(tag)
        return [i]

    def _handle_decl_or_comment(self, s: str, i: int) -> list:
        """Parse '<!--...-->', '<!DOCTYPE...>', or '<![CDATA[...]]>'."""
        n: int = len(s)
        if i + 3 < n and s[i + 1:i + 4] == "!--":
            # Comment
            i = i + 4
            end: int = s.find("-->", i)
            if end < 0:
                self._buf = s[i - 4:]
                return [n]
            comment: str = s[i:end]
            self.handle_comment(comment)
            return [end + 3]
        if i + 8 < n and s[i + 1:i + 9] == "![CDATA[":
            # CDATA
            i = i + 9
            end2: int = s.find("]]>", i)
            if end2 < 0:
                self._buf = s[i - 9:]
                return [n]
            cdata: str = s[i:end2]
            self.handle_data(cdata)
            return [end2 + 3]
        # DOCTYPE or other
        depth: int = 1
        i = i + 1
        while i < n and depth > 0:
            if s[i] == "<":
                depth = depth + 1
            elif s[i] == ">":
                depth = depth - 1
            i = i + 1
        return [i]

    def _handle_pi(self, s: str, i: int) -> list:
        """Parse '<?...?>'."""
        n: int = len(s)
        i = i + 2  # skip '<?'
        end: int = s.find("?>", i)
        if end < 0:
            self._buf = s[i - 2:]
            return [n]
        data: str = s[i:end]
        self.handle_pi(data)
        return [end + 2]

    # ------------------------------------------------------------------
    # Handler methods (override in subclasses)
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list) -> None:
        """Called for each opening tag."""
        pass

    def handle_endtag(self, tag: str) -> None:
        """Called for each closing tag."""
        pass

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        """Called for self-closing tags (default: calls start + end)."""
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        """Called for text content."""
        pass

    def handle_entityref(self, name: str) -> None:
        """Called for named character references like &amp;."""
        pass

    def handle_charref(self, name: str) -> None:
        """Called for decimal/hex character references like &#42; or &#x2a;."""
        pass

    def handle_comment(self, data: str) -> None:
        """Called for HTML comments <!-- ... -->."""
        pass

    def handle_decl(self, decl: str) -> None:
        """Called for <!...> declarations (DOCTYPE etc.)."""
        pass

    def handle_pi(self, data: str) -> None:
        """Called for processing instructions <?...?>."""
        pass

    def unknown_decl(self, data: str) -> None:
        """Called for unrecognized SGML declarations."""
        pass


# ---------------------------------------------------------------------------
# HTML entity unescaping
# ---------------------------------------------------------------------------

_HTML_ENTITIES: dict = {}


def _init_entities() -> None:
    _HTML_ENTITIES["amp"] = "&"
    _HTML_ENTITIES["lt"] = "<"
    _HTML_ENTITIES["gt"] = ">"
    _HTML_ENTITIES["quot"] = '"'
    _HTML_ENTITIES["apos"] = "'"
    _HTML_ENTITIES["nbsp"] = " "
    _HTML_ENTITIES["copy"] = "©"
    _HTML_ENTITIES["reg"] = "®"
    _HTML_ENTITIES["trade"] = "™"
    _HTML_ENTITIES["mdash"] = "—"
    _HTML_ENTITIES["ndash"] = "–"
    _HTML_ENTITIES["hellip"] = "…"
    _HTML_ENTITIES["laquo"] = "«"
    _HTML_ENTITIES["raquo"] = "»"
    _HTML_ENTITIES["euro"] = "€"
    _HTML_ENTITIES["pound"] = "£"
    _HTML_ENTITIES["yen"] = "¥"
    _HTML_ENTITIES["cent"] = "¢"
    _HTML_ENTITIES["lsquo"] = "‘"
    _HTML_ENTITIES["rsquo"] = "’"
    _HTML_ENTITIES["ldquo"] = "“"
    _HTML_ENTITIES["rdquo"] = "”"
    _HTML_ENTITIES["bull"] = "•"
    _HTML_ENTITIES["middot"] = "·"
    _HTML_ENTITIES["emsp"] = " "
    _HTML_ENTITIES["ensp"] = " "
    _HTML_ENTITIES["thinsp"] = " "
    _HTML_ENTITIES["zwnj"] = "‌"
    _HTML_ENTITIES["zwj"] = "‍"
    _HTML_ENTITIES["rlm"] = "‏"
    _HTML_ENTITIES["lrm"] = "‎"
    _HTML_ENTITIES["shy"] = "­"


_init_entities()


def unescape(s: str) -> str:
    """Unescape HTML entities in s."""
    if "&" not in s:
        return s
    result: str = ""
    i: int = 0
    n: int = len(s)
    while i < n:
        if s[i] != "&":
            result = result + s[i]
            i = i + 1
            continue
        # Find ';'
        semi: int = s.find(";", i + 1)
        if semi < 0 or semi - i > 10:
            result = result + s[i]
            i = i + 1
            continue
        entity: str = s[i + 1:semi]
        if entity.startswith("#"):
            # Numeric reference
            num_str: str = entity[1:]
            code: int = 0
            if num_str.startswith("x") or num_str.startswith("X"):
                num_str = num_str[1:]
                # Parse hex
                for ch in num_str:
                    if "0" <= ch <= "9":
                        code = code * 16 + ord(ch) - ord("0")
                    elif "a" <= ch <= "f":
                        code = code * 16 + ord(ch) - ord("a") + 10
                    elif "A" <= ch <= "F":
                        code = code * 16 + ord(ch) - ord("A") + 10
            else:
                code = int(num_str)
            result = result + chr(code)
        elif entity in _HTML_ENTITIES:
            result = result + _HTML_ENTITIES[entity]
        else:
            result = result + "&" + entity + ";"
        i = semi + 1
    return result


def escape(s: str, quote: int = 1) -> str:
    """Escape HTML special characters in s."""
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    if quote:
        s = s.replace('"', "&quot;")
    return s
