"""xml.etree.ElementTree: simple XML processing (accessed as xml_etree).

A pure Python XML parser and element tree API compatible with CPython's
xml.etree.ElementTree module.

Usage::

    import xml_etree as ET

    tree = ET.parse("config.xml")
    root = tree.getroot()
    for child in root:
        print(child.tag, child.attrib, child.text)

    # Programmatic tree construction
    root = ET.Element("root")
    child = ET.SubElement(root, "item", {"id": "1"})
    child.text = "Hello"
    print(ET.tostring(root))
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Element
# ---------------------------------------------------------------------------

class Element:
    """An XML element.

    Attributes:
      tag    -- element tag (str)
      attrib -- element attributes (dict)
      text   -- text before first child (str or None)
      tail   -- text after end tag (str or None)
    """

    def __init__(self, tag: str, attrib: dict = {}) -> None:
        self.tag: str = tag
        self.attrib: dict = {}
        for k in attrib:
            self.attrib[k] = attrib[k]
        self.text: str = ""
        self.tail: str = ""
        self._children: list = []

    def __len__(self) -> int:
        return len(self._children)

    def __getitem__(self, index: int) -> Element:
        return self._children[index]

    def __setitem__(self, index: int, element: Element) -> None:
        self._children[index] = element

    def __iter__(self) -> list:
        return self._children

    def append(self, subelement: Element) -> None:
        self._children.append(subelement)

    def extend(self, elements: list) -> None:
        for e in elements:
            self._children.append(e)

    def insert(self, index: int, subelement: Element) -> None:
        self._children.insert(index, subelement)

    def remove(self, subelement: Element) -> None:
        self._children.remove(subelement)

    def find(self, path: str, namespaces: dict = {}) -> Element:
        """Find first sub-element by tag name."""
        for child in self._children:
            if child.tag == path:
                return child
        return None

    def findall(self, path: str, namespaces: dict = {}) -> list:
        """Find all sub-elements by tag name."""
        result: list = []
        for child in self._children:
            if child.tag == path:
                result.append(child)
        return result

    def findtext(self, path: str, default: str = "",
                 namespaces: dict = {}) -> str:
        """Find first sub-element text by tag name."""
        el: Element = self.find(path)
        if el is None:
            return default
        return el.text if el.text else default

    def iter(self, tag: str = "") -> list:
        """Iterate over all elements in the tree, optionally filtered by tag."""
        result: list = []
        if not tag or self.tag == tag:
            result.append(self)
        for child in self._children:
            for sub in child.iter(tag):
                result.append(sub)
        return result

    def get(self, key: str, default: str = "") -> str:
        """Get attribute value."""
        if key in self.attrib:
            return self.attrib[key]
        return default

    def set(self, key: str, value: str) -> None:
        """Set attribute value."""
        self.attrib[key] = value

    def keys(self) -> list:
        """Return list of attribute names."""
        result: list = []
        for k in self.attrib:
            result.append(k)
        return result

    def items(self) -> list:
        """Return list of [key, value] pairs."""
        result: list = []
        for k in self.attrib:
            result.append([k, self.attrib[k]])
        return result

    def clear(self) -> None:
        """Reset element to empty state."""
        self.attrib = {}
        self.text = ""
        self.tail = ""
        self._children = []

    def copy(self) -> Element:
        """Create a shallow copy."""
        el: Element = Element(self.tag, self.attrib)
        el.text = self.text
        el.tail = self.tail
        for child in self._children:
            el._children.append(child)
        return el

    def __repr__(self) -> str:
        return "<Element '" + self.tag + "' at ...>"


# ---------------------------------------------------------------------------
# ElementTree
# ---------------------------------------------------------------------------

class ElementTree:
    """An XML document tree."""

    def __init__(self, element: Element = None, file: str = "") -> None:
        self._root: Element = element
        if file:
            self.parse(file)

    def getroot(self) -> Element:
        return self._root

    def _setroot(self, element: Element) -> None:
        self._root = element

    def parse(self, source: str, parser: object = None) -> Element:
        """Parse an XML file and set the root element."""
        f = open(source, "r")
        content: str = f.read()
        f.close()
        self._root = fromstring(content)
        return self._root

    def write(self, file_or_filename: str, encoding: str = "us-ascii",
              xml_declaration: int = 0, default_namespace: str = "",
              method: str = "xml", short_empty_elements: int = 1) -> None:
        """Write the XML tree to a file."""
        data: str = tostring(self._root, encoding, method)
        if xml_declaration:
            decl: str = '<?xml version="1.0" encoding="' + encoding + '" ?>\n'
            data = decl + data
        f = open(file_or_filename, "w")
        f.write(data)
        f.close()

    def find(self, path: str, namespaces: dict = {}) -> Element:
        return self._root.find(path)

    def findall(self, path: str, namespaces: dict = {}) -> list:
        return self._root.findall(path)

    def findtext(self, path: str, default: str = "",
                 namespaces: dict = {}) -> str:
        return self._root.findtext(path, default)

    def iter(self, tag: str = "") -> list:
        return self._root.iter(tag)

    def iterfind(self, path: str, namespaces: dict = {}) -> list:
        return self._root.findall(path)


# ---------------------------------------------------------------------------
# Pure Python XML parser
# ---------------------------------------------------------------------------

def _skip_whitespace(s: str, i: int) -> int:
    n: int = len(s)
    while i < n and s[i] in " \t\r\n":
        i = i + 1
    return i


def _read_until(s: str, i: int, stop: str) -> list:
    """Read until stop character. Returns [text, next_i]."""
    start: int = i
    n: int = len(s)
    while i < n and s[i] != stop:
        i = i + 1
    return [s[start:i], i]


def _parse_attr_value(s: str, i: int) -> list:
    """Parse attribute value (quoted). Returns [value, next_i]."""
    n: int = len(s)
    if i >= n:
        return ["", i]
    quote: str = s[i]
    i = i + 1
    start: int = i
    while i < n and s[i] != quote:
        i = i + 1
    value: str = s[start:i]
    if i < n:
        i = i + 1  # skip closing quote
    return [_unescape(value), i]


def _parse_attrs(s: str, i: int, end: str) -> list:
    """Parse attributes from s[i:] until '>' or '/>'. Returns [attrs_dict, next_i, self_closing]."""
    n: int = len(s)
    attrs: dict = {}
    self_closing: int = 0
    while i < n:
        i = _skip_whitespace(s, i)
        if i >= n:
            break
        if s[i] == ">":
            i = i + 1
            break
        if s[i] == "/" and i + 1 < n and s[i + 1] == ">":
            self_closing = 1
            i = i + 2
            break
        # Read attribute name
        start: int = i
        while i < n and s[i] not in " \t\r\n=/>":
            i = i + 1
        name: str = s[start:i]
        if not name:
            i = i + 1
            continue
        i = _skip_whitespace(s, i)
        if i < n and s[i] == "=":
            i = i + 1
            i = _skip_whitespace(s, i)
            result: list = _parse_attr_value(s, i)
            attrs[name] = result[0]
            i = result[1]
        else:
            attrs[name] = "1"
    return [attrs, i, self_closing]


def _unescape(s: str) -> str:
    """Unescape XML entities."""
    s = s.replace("&amp;", "&")
    s = s.replace("&lt;", "<")
    s = s.replace("&gt;", ">")
    s = s.replace("&quot;", '"')
    s = s.replace("&apos;", "'")
    return s


def _parse_element(s: str, i: int) -> list:
    """Parse one XML element starting at '<'. Returns [Element, next_i]."""
    n: int = len(s)
    # Skip '<'
    i = i + 1
    if i >= n:
        return [None, i]
    # Check for processing instruction, comment, CDATA, DOCTYPE
    if s[i] == "?":
        # Processing instruction: skip to '?>'
        while i < n - 1:
            if s[i] == "?" and s[i + 1] == ">":
                i = i + 2
                break
            i = i + 1
        return [None, i]
    if s[i] == "!":
        if i + 2 < n and s[i + 1] == "-" and s[i + 2] == "-":
            # Comment: skip to '-->'
            i = i + 3
            while i < n - 2:
                if s[i] == "-" and s[i + 1] == "-" and s[i + 2] == ">":
                    i = i + 3
                    break
                i = i + 1
            return [None, i]
        if i + 1 < n and s[i + 1:i + 8] == "[CDATA[":
            # CDATA section: skip to ']]>'
            i = i + 8
            while i < n - 2:
                if s[i] == "]" and s[i + 1] == "]" and s[i + 2] == ">":
                    i = i + 3
                    break
                i = i + 1
            return [None, i]
        # DOCTYPE or other: skip to '>'
        while i < n and s[i] != ">":
            i = i + 1
        if i < n:
            i = i + 1
        return [None, i]
    if s[i] == "/":
        # End tag: caller handles this
        return [None, i - 1]
    # Start tag: read tag name
    start: int = i
    while i < n and s[i] not in " \t\r\n/>":
        i = i + 1
    tag: str = s[start:i]
    if not tag:
        return [None, i]
    attr_result: list = _parse_attrs(s, i, ">")
    attrs: dict = attr_result[0]
    i = attr_result[1]
    self_closing: int = attr_result[2]
    el: Element = Element(tag, attrs)
    if self_closing:
        return [el, i]
    # Parse children
    text_buf: str = ""
    while i < n:
        if s[i] == "<":
            if i + 1 < n and s[i + 1] == "/":
                # End tag
                if text_buf:
                    el.text = _unescape(text_buf)
                    text_buf = ""
                i = i + 2  # skip '</'
                while i < n and s[i] != ">":
                    i = i + 1
                if i < n:
                    i = i + 1  # skip '>'
                break
            # Child element or comment/PI
            if text_buf and not el._children:
                el.text = _unescape(text_buf)
                text_buf = ""
            child_result: list = _parse_element(s, i)
            child: Element = child_result[0]
            i = child_result[1]
            if child is not None:
                if el._children and text_buf:
                    prev: Element = el._children[len(el._children) - 1]
                    prev.tail = _unescape(text_buf)
                    text_buf = ""
                el._children.append(child)
        else:
            text_buf = text_buf + s[i]
            i = i + 1
    if text_buf and not el._children:
        el.text = _unescape(text_buf)
    return [el, i]


def _parse_doc(s: str) -> Element:
    """Parse an XML document string and return the root element."""
    n: int = len(s)
    i: int = 0
    root: Element = None
    while i < n:
        i = _skip_whitespace(s, i)
        if i >= n:
            break
        if s[i] == "<":
            result: list = _parse_element(s, i)
            el: Element = result[0]
            i = result[1]
            if el is not None and root is None:
                root = el
        else:
            i = i + 1
    return root


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def Element_factory(tag: str, attrib: dict = {}) -> Element:
    return Element(tag, attrib)


def SubElement(parent: Element, tag: str, attrib: dict = {}) -> Element:
    """Create a subelement and append it to the parent."""
    el: Element = Element(tag, attrib)
    parent.append(el)
    return el


def fromstring(text: str) -> Element:
    """Parse an XML string and return an Element."""
    return _parse_doc(text)


def fromstringlist(sequence: list, parser: object = None) -> Element:
    """Parse a sequence of strings as XML."""
    return fromstring("".join(sequence))


def tostring(element: Element, encoding: str = "us-ascii",
             method: str = "xml", xml_declaration: int = 0,
             default_namespace: str = "", short_empty_elements: int = 1) -> str:
    """Serialize an Element to a string."""
    return _serialize(element)


def _serialize(el: Element) -> str:
    """Recursively serialize an Element to XML string."""
    tag: str = el.tag
    attrs: str = ""
    for k in el.attrib:
        attrs = attrs + " " + k + '="' + _escape_attr(el.attrib[k]) + '"'
    text: str = _escape_text(el.text) if el.text else ""
    children: str = ""
    for child in el._children:
        children = children + _serialize(child)
        if child.tail:
            children = children + _escape_text(child.tail)
    if not text and not children:
        return "<" + tag + attrs + "/>"
    return "<" + tag + attrs + ">" + text + children + "</" + tag + ">"


def _escape_text(s: str) -> str:
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s


def _escape_attr(s: str) -> str:
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    s = s.replace('"', "&quot;")
    return s


def parse(source: str, parser: object = None) -> ElementTree:
    """Parse an XML file and return an ElementTree."""
    return ElementTree(file=source)


def iterparse(source: str, events: list = []) -> list:
    """Parse XML and return list of [event, element] pairs (simplified)."""
    tree: ElementTree = parse(source)
    root: Element = tree.getroot()
    result: list = []
    for el in root.iter(""):
        if "start" in events or not events:
            result.append(["start", el])
        if "end" in events or not events:
            result.append(["end", el])
    return result


def indent(tree: Element, space: str = "  ", level: int = 0) -> None:
    """Add indentation to the tree (in-place, Python 3.9+ API)."""
    i: str = "\n" + level * space
    j: str = "\n" + (level - 1) * space
    if len(tree._children):
        if not tree.text or not tree.text.strip():
            tree.text = i + space
        if not tree.tail or not tree.tail.strip():
            tree.tail = i
        last_idx: int = len(tree._children) - 1
        idx: int = 0
        for child in tree._children:
            indent(child, space, level + 1)
            if idx == last_idx:
                if not child.tail or not child.tail.strip():
                    child.tail = j
            idx = idx + 1
    else:
        if level and (not tree.tail or not tree.tail.strip()):
            tree.tail = i


def register_namespace(prefix: str, uri: str) -> None:
    """Register a namespace prefix (stub)."""
    pass


def canonicalize(xml_data: str, out: object = None, from_file: str = "",
                 with_comments: int = 0, strip_text: int = 0,
                 rewrite_prefixes: int = 0, qname_aware_tags: list = [],
                 qname_aware_attrs: list = [], exclude_attrs: list = [],
                 exclude_tags: list = []) -> str:
    """C14N XML canonicalization (stub — returns input unchanged)."""
    return xml_data


class ParseError(SyntaxError):
    """Raised for XML parse errors."""

    def __init__(self, msg: str = "", position: list = [0, 0]) -> None:
        self.msg: str = msg
        self.position: list = position

    def __str__(self) -> str:
        return "ParseError: " + self.msg + " at line " + str(self.position[0])


# Aliases matching CPython
XML = fromstring
XMLParser = None
TreeBuilder = None
