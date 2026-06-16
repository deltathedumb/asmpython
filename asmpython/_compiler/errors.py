"""Diagnostics infrastructure shared by every compiler phase.

Each phase raises a `CompileError` carrying the source location it failed at
and an optional error code.  The CLI formats these with a caret-pointer so the
user sees exactly where the problem is, instead of a bare exception trace.

Error code scheme
-----------------
Lex errors      L001 – L099
Parse errors    P001 – P099
Semantic errors E001 – E099

Each code maps to a single, stable error *category* so users can look up
"what does E031 mean" without needing to read compiler source.  Run

    asmpython --explain E031

to print the full description for a code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SourcePos:
    line: int
    col: int


# ---------------------------------------------------------------------------
# Error code registry
# ---------------------------------------------------------------------------

class ErrorCode:
    """Named error codes for every distinct compiler diagnostic.

    Codes are stable — once assigned they never change.  The integer value
    encodes the phase via its magnitude:
      1–99   lex (emitted as L001 … L099)
      101–199 parse (emitted as P001 … P099)
      201–399 semantic (emitted as E001 … E199)
    """

    # ---- Lex (L) -----------------------------------------------------------
    L_INCONSISTENT_INDENT   = 1   # L001: inconsistent indentation
    L_UNEXPECTED_CHAR       = 2   # L002: unexpected character
    L_UNTERMINATED_STRING   = 3   # L003: unterminated string literal
    L_NEWLINE_IN_STRING     = 4   # L004: newline inside string literal
    L_UNTERMINATED_FSTRING  = 5   # L005: unterminated f-string
    L_NEWLINE_IN_FSTRING    = 6   # L006: newline inside f-string
    L_INVALID_FLOAT         = 7   # L007: invalid float literal
    L_INVALID_ESCAPE        = 8   # L008: invalid escape sequence
    L_INVALID_INT           = 9   # L009: invalid integer literal
    L_UNTERMINATED_RAW      = 10  # L010: unterminated raw string

    # ---- Parse (P) ---------------------------------------------------------
    P_UNEXPECTED_TOKEN      = 101  # P001: unexpected token
    P_EXPECTED_TOKEN        = 102  # P002: expected a specific token
    P_INVALID_ASSIGN_TARGET = 103  # P003: cannot assign to this expression
    P_INVALID_DECORATOR     = 104  # P004: invalid decorator
    P_INVALID_PATTERN       = 105  # P005: invalid match pattern
    P_MISSING_MODULE        = 106  # P006: expected module name after 'from'
    P_INVALID_DEFAULT       = 107  # P007: invalid default argument
    P_INVALID_ANNOTATION    = 108  # P008: invalid type annotation
    P_MULTILINE_IMPORT      = 109  # P009: multi-line parenthesised import

    # ---- Semantic (E) ------------------------------------------------------
    # Name resolution
    E_UNDEFINED_NAME        = 201  # E001: name is not defined
    E_UNDEFINED_FUNC        = 202  # E002: call to undefined function
    E_REDEFINED_FUNC        = 203  # E003: function redefined in same scope
    E_REDEFINED_CLASS       = 204  # E004: class redefined in same scope
    E_NO_SUCH_MODULE        = 205  # E005: import of unknown module

    # Type errors
    E_TYPE_MISMATCH         = 211  # E011: incompatible types in expression
    E_BINARY_OP_TYPE        = 212  # E012: operator not supported between types
    E_UNARY_OP_TYPE         = 213  # E013: unary operator on wrong type
    E_FSTRING_SEGMENT_TYPE  = 214  # E014: f-string segment has non-scalar type
    E_RETURN_TYPE           = 215  # E015: return value type mismatch
    E_INDEX_TYPE            = 216  # E016: index operand has wrong type
    E_INDEX_OBJECT_TYPE     = 217  # E017: indexing a non-indexable type
    E_ITER_TYPE             = 218  # E018: iterating a non-iterable type
    E_ASSIGN_TYPE           = 219  # E019: assignment type mismatch

    # Call errors
    E_ARG_COUNT             = 221  # E021: wrong number of arguments
    E_ARG_TYPE              = 222  # E022: argument has wrong type
    E_VARARGS_UNPACK        = 223  # E023: *args re-unpacking with unknown types
    E_NOT_CALLABLE          = 224  # E024: calling a non-callable value
    E_FORMAT_ARG_COUNT      = 225  # E025: % format arg count mismatch
    E_FORMAT_ARG_TYPE       = 226  # E026: % format arg type mismatch
    E_FORMAT_LITERAL        = 227  # E027: % format string must be a literal

    # Control flow
    E_BREAK_OUTSIDE_LOOP    = 231  # E031: 'break' outside a loop
    E_CONTINUE_OUTSIDE_LOOP = 232  # E032: 'continue' outside a loop
    E_RETURN_OUTSIDE_FUNC   = 233  # E033: 'return' outside a function

    # Class / attribute
    E_NO_ATTR               = 241  # E041: object has no attribute
    E_NOT_AN_EXCEPTION      = 242  # E042: raised expression is not an exception
    E_INDEX_ASSIGN          = 243  # E043: object does not support index assignment
    E_SUPER_NO_CLASS        = 244  # E044: super() outside a class method
    E_STATIC_NO_CLASS       = 245  # E045: @staticmethod outside a class

    # Collection / structural
    E_HETEROGENEOUS_LIST    = 251  # E051: list literal has mixed types
    E_HETEROGENEOUS_TUPLE   = 252  # E052: tuple literal has mixed types (in)
    E_UNPACK_COUNT          = 253  # E053: unpacking count mismatch
    E_DICT_KEY_TYPE         = 254  # E054: dict operation requires str key
    E_SET_KEY_TYPE          = 255  # E055: set requires str elements (v1)

    # Assembly directives
    E_ASM_OPERAND           = 261  # E061: invalid assembly operand
    E_ASM_REGISTER          = 262  # E062: unrecognised x86-64 register
    E_INCLUDE_ARG           = 263  # E063: include() argument must be a string

    # Misc
    E_ZIP_ARGS              = 271  # E071: zip() arguments must be lists/tuples
    E_ENUMERATE_ARG         = 272  # E072: enumerate() argument must be a list
    E_MATCH_PATTERN         = 273  # E073: unsupported match pattern


# Human-readable description for each code (used by --explain).
ERROR_DESCRIPTIONS: dict[int, str] = {
    # Lex
    ErrorCode.L_INCONSISTENT_INDENT:   "Inconsistent indentation: the indentation level does not match any enclosing block.",
    ErrorCode.L_UNEXPECTED_CHAR:       "Unexpected character: the source contains a character that is not part of any valid token.",
    ErrorCode.L_UNTERMINATED_STRING:   "Unterminated string literal: the closing quote is missing before end-of-line or end-of-file.",
    ErrorCode.L_NEWLINE_IN_STRING:     "Newline inside a single-quoted string literal.  Use a multi-line string (triple quotes) instead.",
    ErrorCode.L_UNTERMINATED_FSTRING:  "Unterminated f-string: the closing quote is missing.",
    ErrorCode.L_NEWLINE_IN_FSTRING:    "Newline inside an f-string.  F-strings must fit on one line.",
    ErrorCode.L_INVALID_FLOAT:         "Invalid floating-point literal.",
    ErrorCode.L_INVALID_ESCAPE:        "Invalid escape sequence inside a string literal.",
    ErrorCode.L_INVALID_INT:           "Invalid integer literal (e.g. a malformed hex, octal, or binary prefix).",
    ErrorCode.L_UNTERMINATED_RAW:      "Unterminated raw string literal.",
    # Parse
    ErrorCode.P_UNEXPECTED_TOKEN:      "Unexpected token: the parser encountered a token that does not fit any valid syntax rule here.",
    ErrorCode.P_EXPECTED_TOKEN:        "Expected a specific token (keyword, operator, or punctuation) but found something else.",
    ErrorCode.P_INVALID_ASSIGN_TARGET: "Cannot assign to this expression.  Only names, subscripts, and attribute paths are valid assignment targets.",
    ErrorCode.P_INVALID_DECORATOR:     "Invalid decorator expression.",
    ErrorCode.P_INVALID_PATTERN:       "Invalid match/case pattern.",
    ErrorCode.P_MISSING_MODULE:        "Expected a module name after 'from' in an import statement.",
    ErrorCode.P_INVALID_DEFAULT:       "Invalid default argument value.  Only literals and simple names are supported as defaults.",
    ErrorCode.P_INVALID_ANNOTATION:    "Invalid type annotation.",
    ErrorCode.P_MULTILINE_IMPORT:      "Multi-line parenthesised imports are not supported.  Use one 'from X import Y' per line.",
    # Semantic – name resolution
    ErrorCode.E_UNDEFINED_NAME:        "Name is not defined in the current scope.  Check for typos or missing imports.",
    ErrorCode.E_UNDEFINED_FUNC:        "Call to an undefined function.  The function may not be imported or may be defined after the call site.",
    ErrorCode.E_REDEFINED_FUNC:        "Function is defined more than once in the same scope.  Each function name must be unique.",
    ErrorCode.E_REDEFINED_CLASS:       "Class is defined more than once in the same scope.",
    ErrorCode.E_NO_SUCH_MODULE:        "Import of an unknown or unsupported module.",
    # Semantic – types
    ErrorCode.E_TYPE_MISMATCH:         "Type mismatch: the expression type does not match what is expected here.",
    ErrorCode.E_BINARY_OP_TYPE:        "Binary operator not supported between the given operand types.",
    ErrorCode.E_UNARY_OP_TYPE:         "Unary operator applied to an incompatible type.",
    ErrorCode.E_FSTRING_SEGMENT_TYPE:  "F-string segment has a non-scalar type.  Only int, float, str, or class instances can appear inside f-strings.",
    ErrorCode.E_RETURN_TYPE:           "Return value type does not match the declared return type.",
    ErrorCode.E_INDEX_TYPE:            "Index operand has the wrong type.",
    ErrorCode.E_INDEX_OBJECT_TYPE:     "Indexing a value that does not support subscript access.",
    ErrorCode.E_ITER_TYPE:             "Iterating over a value that is not iterable.",
    ErrorCode.E_ASSIGN_TYPE:           "Assignment type mismatch: the right-hand side type is not compatible with the target.",
    # Semantic – calls
    ErrorCode.E_ARG_COUNT:             "Wrong number of arguments passed to function or method.",
    ErrorCode.E_ARG_TYPE:              "Argument has the wrong type.",
    ErrorCode.E_VARARGS_UNPACK:        "*args re-unpacking requires a tuple with known element types at compile time.",
    ErrorCode.E_NOT_CALLABLE:          "Attempting to call a value that is not a function or class.",
    ErrorCode.E_FORMAT_ARG_COUNT:      "% format string expects a different number of arguments than were provided.",
    ErrorCode.E_FORMAT_ARG_TYPE:       "% format conversion requires a different argument type.",
    ErrorCode.E_FORMAT_LITERAL:        "% string formatting requires a literal format string.",
    # Semantic – control flow
    ErrorCode.E_BREAK_OUTSIDE_LOOP:    "'break' used outside a loop.",
    ErrorCode.E_CONTINUE_OUTSIDE_LOOP: "'continue' used outside a loop.",
    ErrorCode.E_RETURN_OUTSIDE_FUNC:   "'return' used outside a function definition.",
    # Semantic – class / attribute
    ErrorCode.E_NO_ATTR:               "The object does not have the referenced attribute.",
    ErrorCode.E_NOT_AN_EXCEPTION:      "The raised expression is not an exception type.",
    ErrorCode.E_INDEX_ASSIGN:          "The object does not support index assignment.",
    ErrorCode.E_SUPER_NO_CLASS:        "super() used outside a class method.",
    ErrorCode.E_STATIC_NO_CLASS:       "@staticmethod used outside a class.",
    # Semantic – collections
    ErrorCode.E_HETEROGENEOUS_LIST:    "List literal contains elements of mixed types.  asmpython lists must be homogeneous.",
    ErrorCode.E_HETEROGENEOUS_TUPLE:   "'in' on a tuple with mixed element types is not supported.",
    ErrorCode.E_UNPACK_COUNT:          "Unpacking assignment count mismatch.",
    ErrorCode.E_DICT_KEY_TYPE:         "Dict operation requires a str key.",
    ErrorCode.E_SET_KEY_TYPE:          "Set elements must be strings in asmpython v1.",
    # Semantic – assembly
    ErrorCode.E_ASM_OPERAND:           "Invalid assembly operand string.",
    ErrorCode.E_ASM_REGISTER:          "Unrecognised x86-64 register name.",
    ErrorCode.E_INCLUDE_ARG:           "include() argument must be a string literal package name.",
    # Semantic – misc
    ErrorCode.E_ZIP_ARGS:              "zip() arguments must be lists or tuples.",
    ErrorCode.E_ENUMERATE_ARG:         "enumerate() argument must be a list.",
    ErrorCode.E_MATCH_PATTERN:         "Unsupported match/case pattern construct.",
}


def _code_label(code: int) -> str:
    """Return the human-readable code label, e.g. 1 -> 'L001', 212 -> 'E012'."""
    if code < 100:
        return f"L{code:03d}"
    if code < 200:
        return f"P{code - 100:03d}"
    return f"E{code - 200:03d}"


class CompileError(Exception):
    """Base class for any user-facing compile-time failure.

    Carries a SourcePos so the driver can render a pointer into the source.
    Phase tag distinguishes 'lex error' vs 'parse error' vs 'name error' etc.
    An optional integer `code` (from `ErrorCode`) is displayed as [L001] /
    [P001] / [E001] in the error output and can be passed to ``--explain``.
    """

    def __init__(
        self,
        phase: str,
        message: str,
        pos: Optional[SourcePos] = None,
        code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.message = message
        self.pos = pos
        self.code = code

    def format(self, source: str, filename: str = "<input>") -> str:
        out: list[str] = []
        code_tag = f"[{_code_label(self.code)}] " if self.code is not None else ""
        if self.pos is not None:
            out.append(
                f"{filename}:{self.pos.line}:{self.pos.col}: {self.phase} error: "
                f"{code_tag}{self.message}"
            )
            line_text = _get_line(source, self.pos.line)
            if line_text is not None:
                out.append("  " + line_text.rstrip("\n"))
                out.append("  " + " " * (self.pos.col - 1) + "^")
        else:
            out.append(f"{filename}: {self.phase} error: {code_tag}{self.message}")
        return "\n".join(out)


class LexError(CompileError):
    def __init__(
        self,
        message: str,
        pos: Optional[SourcePos] = None,
        code: Optional[int] = None,
    ) -> None:
        super().__init__("lex", message, pos, code)


class ParseError(CompileError):
    def __init__(
        self,
        message: str,
        pos: Optional[SourcePos] = None,
        code: Optional[int] = None,
    ) -> None:
        super().__init__("parse", message, pos, code)


class SemaError(CompileError):
    def __init__(
        self,
        message: str,
        pos: Optional[SourcePos] = None,
        code: Optional[int] = None,
    ) -> None:
        super().__init__("semantic", message, pos, code)


class MultiSemaError(Exception):
    """Raised when --all-errors mode collects more than one sema error.

    `errors` is a non-empty list of SemaError instances in source order.
    The first error is also available as `errors[0]` for callers that want
    to surface a single representative diagnostic.
    """

    def __init__(self, errors: "list[SemaError]") -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} semantic error(s)")

    def format_all(self, src: str, filename: str) -> str:
        """Format every collected error, separated by blank lines."""
        parts: list[str] = []
        for e in self.errors:
            parts.append(e.format(src, filename))
        return "\n".join(parts)


def explain(code_label: str) -> str:
    """Return the description for a code label like 'E014' or 'L001'.

    Returns an empty string if the code is not recognised.
    """
    label = code_label.upper().strip()
    if not label:
        return ""
    prefix = label[0]
    try:
        n = int(label[1:])
    except ValueError:
        return ""
    if prefix == "L":
        key = n
    elif prefix == "P":
        key = n + 100
    elif prefix == "E":
        key = n + 200
    else:
        return ""
    desc = ERROR_DESCRIPTIONS.get(key)
    if desc is None:
        return ""
    return f"[{label}] {desc}"


def _get_line(source: str, lineno: int) -> Optional[str]:
    lines = source.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return None
