"""html module: HTML escape/unescape utilities."""
from __future__ import annotations


def escape(s: str, quote: int = 1) -> str:
    """Replace &, <, > and (optionally) \" and ' with HTML entities."""
    result: str = ""
    i: int = 0
    n: int = len(s)
    while i < n:
        c: str = s[i]
        if c == "&":
            result = result + "&amp;"
        elif c == "<":
            result = result + "&lt;"
        elif c == ">":
            result = result + "&gt;"
        elif c == '"' and quote:
            result = result + "&quot;"
        elif c == "'" and quote:
            result = result + "&#x27;"
        else:
            result = result + c
        i = i + 1
    return result


def unescape(s: str) -> str:
    """Convert HTML entities to plain text."""
    result: str = ""
    i: int = 0
    n: int = len(s)
    while i < n:
        if s[i] == "&":
            j: int = i + 1
            while j < n and s[j] != ";":
                j = j + 1
            if j < n:
                entity: str = s[i + 1:j]
                if entity == "amp":
                    result = result + "&"
                elif entity == "lt":
                    result = result + "<"
                elif entity == "gt":
                    result = result + ">"
                elif entity == "quot":
                    result = result + '"'
                elif entity == "apos" or entity == "#x27":
                    result = result + "'"
                elif entity == "nbsp":
                    result = result + " "
                elif entity == "copy":
                    result = result + "(c)"
                elif entity == "reg":
                    result = result + "(R)"
                elif entity == "trade":
                    result = result + "(TM)"
                elif entity == "mdash":
                    result = result + "--"
                elif entity == "ndash":
                    result = result + "-"
                elif entity == "laquo":
                    result = result + "<<"
                elif entity == "raquo":
                    result = result + ">>"
                elif len(entity) > 1 and entity[0] == "#":
                    code_str: str = entity[1:]
                    code: int = int(code_str)
                    result = result + chr(code)
                else:
                    result = result + s[i:j + 1]
                i = j + 1
            else:
                result = result + s[i]
                i = i + 1
        else:
            result = result + s[i]
            i = i + 1
    return result
