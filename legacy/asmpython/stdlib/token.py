"""token: Constants representing Python token types.

These match CPython's token module constants exactly.
"""
from __future__ import annotations

ENDMARKER: int = 0
NAME: int = 1
NUMBER: int = 2
STRING: int = 3
NEWLINE: int = 4
NL: int = 61
INDENT: int = 5
DEDENT: int = 6
LPAR: int = 7
RPAR: int = 8
LSQB: int = 9
RSQB: int = 10
COLON: int = 11
COMMA: int = 12
SEMI: int = 13
PLUS: int = 14
MINUS: int = 15
STAR: int = 16
SLASH: int = 17
VBAR: int = 18
AMPER: int = 19
LESS: int = 20
GREATER: int = 21
EQUAL: int = 22
DOT: int = 23
PERCENT: int = 24
LBRACE: int = 25
RBRACE: int = 26
EQEQUAL: int = 27
NOTEQUAL: int = 28
LESSEQUAL: int = 29
GREATEREQUAL: int = 30
TILDE: int = 31
CIRCUMFLEX: int = 32
LEFTSHIFT: int = 33
RIGHTSHIFT: int = 34
DOUBLESTAR: int = 35
PLUSEQUAL: int = 36
MINEQUAL: int = 37
STAREQUAL: int = 38
SLASHEQUAL: int = 39
PERCENTEQUAL: int = 40
AMPEREQUAL: int = 41
VBAREQUAL: int = 42
CIRCUMFLEXEQUAL: int = 43
LEFTSHIFTEQUAL: int = 44
RIGHTSHIFTEQUAL: int = 45
DOUBLESTAREQUAL: int = 46
DOUBLESLASH: int = 47
DOUBLESLASHEQUAL: int = 48
AT: int = 49
ATEQUAL: int = 50
RARROW: int = 51
ELLIPSIS: int = 52
COLONEQUAL: int = 53
OP: int = 54
ERRORTOKEN: int = 59
COMMENT: int = 60
ENCODING: int = 62
TYPE_IGNORE: int = 63
TYPE_COMMENT: int = 64
SOFT_KEYWORD: int = 65
FSTRING_START: int = 66
FSTRING_MIDDLE: int = 67
FSTRING_END: int = 68
N_TOKENS: int = 69
NT_OFFSET: int = 256

# String names for token types
tok_name: dict = {}


def _init_tok_name() -> None:
    tok_name["0"] = "ENDMARKER"
    tok_name["1"] = "NAME"
    tok_name["2"] = "NUMBER"
    tok_name["3"] = "STRING"
    tok_name["4"] = "NEWLINE"
    tok_name["5"] = "INDENT"
    tok_name["6"] = "DEDENT"
    tok_name["7"] = "LPAR"
    tok_name["8"] = "RPAR"
    tok_name["9"] = "LSQB"
    tok_name["10"] = "RSQB"
    tok_name["11"] = "COLON"
    tok_name["12"] = "COMMA"
    tok_name["13"] = "SEMI"
    tok_name["14"] = "PLUS"
    tok_name["15"] = "MINUS"
    tok_name["16"] = "STAR"
    tok_name["17"] = "SLASH"
    tok_name["18"] = "VBAR"
    tok_name["19"] = "AMPER"
    tok_name["20"] = "LESS"
    tok_name["21"] = "GREATER"
    tok_name["22"] = "EQUAL"
    tok_name["23"] = "DOT"
    tok_name["24"] = "PERCENT"
    tok_name["25"] = "LBRACE"
    tok_name["26"] = "RBRACE"
    tok_name["27"] = "EQEQUAL"
    tok_name["28"] = "NOTEQUAL"
    tok_name["29"] = "LESSEQUAL"
    tok_name["30"] = "GREATEREQUAL"
    tok_name["31"] = "TILDE"
    tok_name["32"] = "CIRCUMFLEX"
    tok_name["33"] = "LEFTSHIFT"
    tok_name["34"] = "RIGHTSHIFT"
    tok_name["35"] = "DOUBLESTAR"
    tok_name["36"] = "PLUSEQUAL"
    tok_name["37"] = "MINEQUAL"
    tok_name["38"] = "STAREQUAL"
    tok_name["39"] = "SLASHEQUAL"
    tok_name["40"] = "PERCENTEQUAL"
    tok_name["54"] = "OP"
    tok_name["59"] = "ERRORTOKEN"
    tok_name["60"] = "COMMENT"
    tok_name["61"] = "NL"
    tok_name["62"] = "ENCODING"


_init_tok_name()


def ISTERMINAL(x: int) -> int:
    """Return 1 if x is a terminal token type."""
    return x < NT_OFFSET


def ISNONTERMINAL(x: int) -> int:
    """Return 1 if x is a non-terminal token type."""
    return x >= NT_OFFSET


def ISEOF(x: int) -> int:
    """Return 1 if x is ENDMARKER."""
    return x == ENDMARKER


def tok_name_of(x: int) -> str:
    """Return the name of token type x."""
    key: str = str(x)
    if key in tok_name:
        return tok_name[key]
    return "UNKNOWN"
