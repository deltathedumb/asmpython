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
    P_CONST_NO_INITIALIZER  = 110  # P010: const declaration missing an initializer
    P_UNKNOWN_EXTENSION     = 111  # P011: --ext names an unknown extension
    P_EXTENSION_SCOPE       = 112  # P012: const used outside module scope
    # P013/P014/P017 are reachable only via direct ExtensionContext.activate/
    # retract() API use (see tests/test_extensions.py) -- --ext is deduped
    # into a set before activation and has no in-source retraction, so a real
    # CLI invocation can no longer trigger a duplicate-activation or
    # retract-related diagnostic. Codes are kept allocated for API stability
    # and because a future CLI feature (per-extension version pins, etc.)
    # could make them reachable again.
    P_EXTENSION_ALREADY_ACTIVE = 113  # P013: activate() called on an already-active extension
    P_EXTENSION_NOT_ACTIVE  = 114  # P014: retract() called on an extension that isn't active
    P_EXTENSION_CONFLICT    = 115  # P015: extension conflicts with another active extension
    P_EXTENSION_MISSING_DEP = 116  # P016: extension activation is missing a dependency
    P_EXTENSION_RETRACT_BLOCKED = 117  # P017: retract() blocked by a dependent active extension
    P_CONST_WITHOUT_EXTENSION  = 118  # P018: const used without --ext constants
    # P019 intentionally unused: access modifiers are decorator-shaped, so
    # their "used without extension" diagnostic is a semantic-phase check
    # (E087), not a parser-phase one -- unlike const/final/sealed/enum,
    # which are all new statement-prefix shapes with real parse-time lookahead.
    P_FINAL_WITHOUT_EXTENSION  = 120  # P020: 'final class' used without --ext final
    P_SEALED_WITHOUT_EXTENSION = 121  # P021: 'sealed class' used without --ext sealed
    P_ENUM_WITHOUT_EXTENSION   = 122  # P022: 'enum' declaration used without --ext enum
    P_INTERFACE_WITHOUT_EXTENSION = 123  # P023: 'interface' declaration used without --ext interface
    P_INTERFACE_STUB_BODY      = 124  # P024: interface method stub body contains real code
    P_ASSIGN_DECORATOR_UNSUPPORTED_TARGET = 125  # P025: @decorator above a non-assignment or parallel-tuple statement

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

    # Compiler extensions (constants)
    E_CONST_REASSIGNED      = 281  # E081: cannot reassign/rebind a const name
    E_CONST_REDEFINED       = 282  # E082: const name collides with a function or class

    # Compiler extensions (wave 1: access, final, sealed, enum, immutable,
    # exhaustive_switch). See docs/EXTENSIONS.md.
    E_PRIVATE_ACCESS_VIOLATION   = 283  # E083: private member accessed from outside its class
    E_PROTECTED_ACCESS_VIOLATION = 284  # E084: protected member accessed from outside its class or subclasses
    E_FINAL_CLASS_SUBCLASSED     = 285  # E085: cannot subclass a final class
    E_FINAL_METHOD_OVERRIDDEN    = 286  # E086: cannot override a final method
    E_DECORATOR_WITHOUT_EXTENSION = 287  # E087: decorator requires a compiler extension that isn't active
    E_SEALED_SUBCLASS_NOT_PERMITTED = 288  # E088: class is not in its sealed parent's permits list
    E_NONEXHAUSTIVE_MATCH        = 289  # E089: match/case does not cover every case
    E_IMMUTABLE_FIELD_REASSIGNED = 290  # E090: cannot assign to an immutable field outside __init__
    E_ENUM_REDEFINED             = 291  # E091: enum name collides with a function or class
    E_ENUM_UNKNOWN_MEMBER        = 292  # E092: no such member on this enum type
    E_ENUM_TYPE_MISMATCH         = 293  # E093: comparing members of two different enum types

    # Compiler extensions (wave 2: readonly_params, const_params,
    # no_global_mutation, no_shadowing, must_use, no_implicit_any,
    # interface, assign_decorators, overload). See docs/EXTENSIONS.md.
    E_READONLY_PARAM_REASSIGNED  = 294  # E094: cannot reassign a @readonly/const_params-locked parameter
    E_READONLY_UNKNOWN_PARAM     = 295  # E095: @readonly names a parameter that doesn't exist
    E_UNDECLARED_GLOBAL_MUTATION = 296  # E096: reassigning a module-level name without 'global'
    E_SHADOWED_GLOBAL            = 297  # E097: a local/param name shadows a module-level name (or a captured free variable)
    E_MUST_USE_DISCARDED         = 298  # E098: @must_use function's return value discarded as a bare statement
    E_IMPLICIT_ANY_PARAM         = 299  # E099: parameter has no annotation, default, or inferrable usage
    E_IMPLICIT_ANY_ASSIGN        = 300  # E100: assignment's value has no inferrable concrete type
    E_INTERFACE_REDEFINED        = 301  # E101: interface name collides with a function or class
    E_INTERFACE_UNKNOWN          = 302  # E102: interface=Name names an undeclared interface
    E_INTERFACE_METHOD_MISSING   = 303  # E103: class does not implement a required interface method
    E_INTERFACE_METHOD_MISMATCH  = 304  # E104: implemented method's signature doesn't match the interface stub
    E_OVERLOAD_AMBIGUOUS         = 305  # E105: call matches two or more @overload signatures equally well
    E_OVERLOAD_NO_MATCH          = 306  # E106: call matches no @overload signature
    E_OVERLOAD_INCOMPATIBLE      = 307  # E107: two @overload signatures are indistinguishable

    # Name resolution / redefinition (batch 2: coding the remaining 217
    # previously-uncoded sema.py call sites, see commit that introduced
    # these -- message wording is unchanged, only code= was added)
    E_CLASS_NAME_COLLISION       = 308  # E108: class name collides with an existing function/class/enum/builtin
    E_INHERITANCE_CYCLE          = 309  # E109: class inheritance chain cycles back to itself

    # Function/method shape
    E_MISSING_SELF_PARAM         = 310  # E110: instance method's first parameter must be 'self'
    E_DUNDER_SIGNATURE           = 311  # E111: dunder method must take exactly (self, other)
    E_METHOD_NEEDS_INSTANCE      = 312  # E112: method needs an instance receiver, not a static/classmethod call
    E_NO_METHOD                  = 313  # E113: object's type has no method of this name

    # Unpacking / assignment
    E_STARRED_ASSIGN_NOT_LIST    = 314  # E114: starred assignment requires a list on the right-hand side
    E_TUPLE_ASSIGN_MIXED_TARGETS = 315  # E115: tuple assign mixing subscript/attribute targets needs the parallel form
    E_UNPACK_TARGET_COUNT        = 316  # E116: unpacking target count doesn't match the values produced
    E_UNPACK_NOT_ITERABLE        = 317  # E117: cannot unpack a non-iterable object

    # Attribute / property / context manager
    E_PROPERTY_NO_SETTER         = 318  # E118: cannot assign to a property that has no setter
    E_NOT_A_CONTEXT_MANAGER      = 319  # E119: object does not support the context manager protocol
    E_MODULE_NO_CALLABLE         = 320  # E120: module has no callable of this name

    # Match/case patterns
    E_OR_PATTERN_CAPTURE         = 321  # E121: capture pattern not allowed inside an or-pattern
    E_MATCH_ARGS_MISSING         = 322  # E122: class used in a positional pattern must define __match_args__
    E_MATCH_ARGS_TOO_MANY        = 323  # E123: too many positional patterns for __match_args__

    # Containment / comparison
    E_CONTAINS_TYPE_MISMATCH     = 324  # E124: 'in'/'not in' needle type doesn't match the container's element type
    E_NO_CONTAINS_METHOD         = 325  # E125: class used with 'in'/'not in' must define __contains__
    E_STRING_COMPARISON_OP       = 326  # E126: comparison operator not supported between strings
    E_UNCOMPARABLE_TYPES         = 327  # E127: these two types cannot be compared with this operator

    # Container literals / spreads
    E_SPREAD_NOT_ITERABLE        = 328  # E128: list unpacking (*expr in a list literal) requires a list or tuple
    E_DICT_UNPACK_TYPE           = 329  # E129: dict unpacking (**expr in a dict literal) requires a dict

    # Indexing
    E_TUPLE_INDEX_RANGE          = 330  # E130: constant tuple index out of range for this tuple's length
    E_TUPLE_INDEX_NOT_CONST      = 331  # E131: indexing a heterogeneous tuple requires a constant index

    # Method/function call errors on builtins
    E_LIST_ELEMENT_TYPE_UNSUPPORTED = 332  # E132: value's type not supported as a list element
    E_BAD_FORMAT_STRING          = 333  # E133: '%' format string has an invalid/unsupported conversion specifier
    E_SORT_KEY_ARITY             = 334  # E134: sort key= function must take exactly one argument
    E_SORT_KEY_TYPE              = 335  # E135: sort key= must be a lambda literal or a name bound to one
    E_FORMAT_UNKNOWN_FIELD       = 336  # E136: str.format() field name has no matching keyword argument
    E_FORMAT_INDEX_RANGE         = 337  # E137: str.format() positional field index is out of range
    E_DSTAR_NOT_DICT             = 338  # E138: '**expr' call argument must evaluate to a dict
    E_KEY_UNSUPPORTED_FORM       = 339  # E139: key= is only supported for the single-iterable call form
    E_DSTAR_NO_PARAM_LIST        = 340  # E140: '**expr' expansion requires a statically known parameter list

    # Batch 3: remaining uncoded call sites (mlang FFI, internal invariants,
    # collection-element-type coverage, interpreter-only builtins). Message
    # wording unchanged, only code= was added.
    E_MLANG_INVALID_ARG          = 341  # E141: mlang Code(...)/Sig(...) argument has an invalid shape
    E_MLANG_COMPILE_FAILED       = 342  # E142: mlang Code(...) source failed to compile
    E_BUILTIN_REDEFINED          = 343  # E143: a top-level def/class redefines a builtin name
    E_TUPLE_ASSIGN_VALUE_TYPE    = 344  # E144: parallel tuple-assign value has an unsupported type
    E_INTERNAL_UNHANDLED_NODE    = 345  # E145: internal: compiler encountered an AST node kind it doesn't handle
    E_TUPLE_ELEMENT_TYPE_UNSUPPORTED = 346  # E146: tuple literal element has an unsupported type
    E_DICT_VALUE_TYPE_UNSUPPORTED = 347  # E147: dict value has an unsupported type
    E_DICT_VALUE_TYPE_MIXED      = 348  # E148: dict literal mixes incompatible value types
    E_INTERPRETER_ONLY_FEATURE   = 349  # E149: this call requires a Python interpreter and can't be compiled natively
    E_MLANG_UNKNOWN_EXPORT       = 350  # E150: mlang Code(...) has no export of this name
    E_RSPLIT_MAXSPLIT            = 351  # E151: str.rsplit() maxsplit argument must be the literal 1
    E_FORMAT_FIELD_UNSUPPORTED   = 352  # E152: str.format() field spec uses an unsupported form


def _code_label(code: int) -> str:
    """Return the human-readable code label, e.g. 1 -> 'L001', 212 -> 'E012'."""
    if code < 100:
        return f"L{code:03d}"
    if code < 200:
        return f"P{code - 100:03d}"
    return f"E{code - 200:03d}"


# Human-readable description for each code (used by --explain). Keyed by the
# string label (e.g. "L001") rather than the raw int: asmpython's runtime
# dict implementation always hashes keys as strings, so an int-keyed literal
# this large silently corrupts (every key gets truncated to a tiny integer
# treated as a garbage string pointer, crashing the first dict op against it
# at runtime) — see the self-host segfault this table caused.
ERROR_DESCRIPTIONS: dict[str, str] = {
    # Lex
    _code_label(ErrorCode.L_INCONSISTENT_INDENT):   "Inconsistent indentation: the indentation level does not match any enclosing block.",
    _code_label(ErrorCode.L_UNEXPECTED_CHAR):       "Unexpected character: the source contains a character that is not part of any valid token.",
    _code_label(ErrorCode.L_UNTERMINATED_STRING):   "Unterminated string literal: the closing quote is missing before end-of-line or end-of-file.",
    _code_label(ErrorCode.L_NEWLINE_IN_STRING):     "Newline inside a single-quoted string literal.  Use a multi-line string (triple quotes) instead.",
    _code_label(ErrorCode.L_UNTERMINATED_FSTRING):  "Unterminated f-string: the closing quote is missing.",
    _code_label(ErrorCode.L_NEWLINE_IN_FSTRING):    "Newline inside an f-string.  F-strings must fit on one line.",
    _code_label(ErrorCode.L_INVALID_FLOAT):         "Invalid floating-point literal.",
    _code_label(ErrorCode.L_INVALID_ESCAPE):        "Invalid escape sequence inside a string literal.",
    _code_label(ErrorCode.L_INVALID_INT):           "Invalid integer literal (e.g. a malformed hex, octal, or binary prefix).",
    _code_label(ErrorCode.L_UNTERMINATED_RAW):      "Unterminated raw string literal.",
    # Parse
    _code_label(ErrorCode.P_UNEXPECTED_TOKEN):      "Unexpected token: the parser encountered a token that does not fit any valid syntax rule here.",
    _code_label(ErrorCode.P_EXPECTED_TOKEN):        "Expected a specific token (keyword, operator, or punctuation) but found something else.",
    _code_label(ErrorCode.P_INVALID_ASSIGN_TARGET): "Cannot assign to this expression.  Only names, subscripts, and attribute paths are valid assignment targets.",
    _code_label(ErrorCode.P_INVALID_DECORATOR):     "Invalid decorator expression.",
    _code_label(ErrorCode.P_INVALID_PATTERN):       "Invalid match/case pattern.",
    _code_label(ErrorCode.P_MISSING_MODULE):        "Expected a module name after 'from' in an import statement.",
    _code_label(ErrorCode.P_INVALID_DEFAULT):       "Invalid default argument value.  Only literals and simple names are supported as defaults.",
    _code_label(ErrorCode.P_INVALID_ANNOTATION):    "Invalid type annotation.",
    _code_label(ErrorCode.P_MULTILINE_IMPORT):      "Multi-line parenthesised imports are not supported.  Use one 'from X import Y' per line.",
    _code_label(ErrorCode.P_CONST_NO_INITIALIZER):  "A 'const' declaration requires an initializer.  'const NAME' alone is invalid; write 'const NAME = value'.",
    _code_label(ErrorCode.P_UNKNOWN_EXTENSION):     "'--ext' names an extension that is not registered.",
    _code_label(ErrorCode.P_EXTENSION_SCOPE):       "'const' is only valid at module scope.",
    _code_label(ErrorCode.P_EXTENSION_ALREADY_ACTIVE): "An extension was activated twice for the same compile.",
    _code_label(ErrorCode.P_EXTENSION_NOT_ACTIVE):  "An extension was retracted that was not active.",
    _code_label(ErrorCode.P_EXTENSION_CONFLICT):    "The extension conflicts with another extension that is already active.",
    _code_label(ErrorCode.P_EXTENSION_MISSING_DEP): "The extension requires another extension that is not active and could not be loaded.",
    _code_label(ErrorCode.P_EXTENSION_RETRACT_BLOCKED): "Cannot retract this extension: another active extension depends on it.",
    _code_label(ErrorCode.P_CONST_WITHOUT_EXTENSION): "'const' declarations require the 'constants' extension.  Add '--ext constants' to the compile command.",
    _code_label(ErrorCode.P_FINAL_WITHOUT_EXTENSION): "'final class' requires the 'final' extension.  Add '--ext final' to the compile command.",
    _code_label(ErrorCode.P_SEALED_WITHOUT_EXTENSION): "'sealed class' requires the 'sealed' extension.  Add '--ext sealed' to the compile command.",
    _code_label(ErrorCode.P_ENUM_WITHOUT_EXTENSION): "'enum' declarations require the 'enum' extension.  Add '--ext enum' to the compile command.",
    _code_label(ErrorCode.P_INTERFACE_WITHOUT_EXTENSION): "'interface' declarations require the 'interface' extension.  Add '--ext interface' to the compile command.",
    _code_label(ErrorCode.P_INTERFACE_STUB_BODY): "An 'interface' method stub's body must be exactly 'pass' -- no real statements.",
    _code_label(ErrorCode.P_ASSIGN_DECORATOR_UNSUPPORTED_TARGET): "'@decorator' above an assignment only supports a single-target or single-call tuple-unpack assignment.",
    # Semantic – name resolution
    _code_label(ErrorCode.E_UNDEFINED_NAME):        "Name is not defined in the current scope.  Check for typos or missing imports.",
    _code_label(ErrorCode.E_UNDEFINED_FUNC):        "Call to an undefined function.  The function may not be imported or may be defined after the call site.",
    _code_label(ErrorCode.E_REDEFINED_FUNC):        "Function is defined more than once in the same scope.  Each function name must be unique.",
    _code_label(ErrorCode.E_REDEFINED_CLASS):       "Class is defined more than once in the same scope.",
    _code_label(ErrorCode.E_NO_SUCH_MODULE):        "Import of an unknown or unsupported module.",
    # Semantic – types
    _code_label(ErrorCode.E_TYPE_MISMATCH):         "Type mismatch: the expression type does not match what is expected here.",
    _code_label(ErrorCode.E_BINARY_OP_TYPE):        "Binary operator not supported between the given operand types.",
    _code_label(ErrorCode.E_UNARY_OP_TYPE):         "Unary operator applied to an incompatible type.",
    _code_label(ErrorCode.E_FSTRING_SEGMENT_TYPE):  "F-string segment has a non-scalar type.  Only int, float, str, or class instances can appear inside f-strings.",
    _code_label(ErrorCode.E_RETURN_TYPE):           "Return value type does not match the declared return type.",
    _code_label(ErrorCode.E_INDEX_TYPE):            "Index operand has the wrong type.",
    _code_label(ErrorCode.E_INDEX_OBJECT_TYPE):     "Indexing a value that does not support subscript access.",
    _code_label(ErrorCode.E_ITER_TYPE):             "Iterating over a value that is not iterable.",
    _code_label(ErrorCode.E_ASSIGN_TYPE):           "Assignment type mismatch: the right-hand side type is not compatible with the target.",
    # Semantic – calls
    _code_label(ErrorCode.E_ARG_COUNT):             "Wrong number of arguments passed to function or method.",
    _code_label(ErrorCode.E_ARG_TYPE):              "Argument has the wrong type.",
    _code_label(ErrorCode.E_VARARGS_UNPACK):        "*args re-unpacking requires a tuple with known element types at compile time.",
    _code_label(ErrorCode.E_NOT_CALLABLE):          "Attempting to call a value that is not a function or class.",
    _code_label(ErrorCode.E_FORMAT_ARG_COUNT):      "% format string expects a different number of arguments than were provided.",
    _code_label(ErrorCode.E_FORMAT_ARG_TYPE):       "% format conversion requires a different argument type.",
    _code_label(ErrorCode.E_FORMAT_LITERAL):        "% string formatting requires a literal format string.",
    # Semantic – control flow
    _code_label(ErrorCode.E_BREAK_OUTSIDE_LOOP):    "'break' used outside a loop.",
    _code_label(ErrorCode.E_CONTINUE_OUTSIDE_LOOP): "'continue' used outside a loop.",
    _code_label(ErrorCode.E_RETURN_OUTSIDE_FUNC):   "'return' used outside a function definition.",
    # Semantic – class / attribute
    _code_label(ErrorCode.E_NO_ATTR):               "The object does not have the referenced attribute.",
    _code_label(ErrorCode.E_NOT_AN_EXCEPTION):      "The raised expression is not an exception type.",
    _code_label(ErrorCode.E_INDEX_ASSIGN):          "The object does not support index assignment.",
    _code_label(ErrorCode.E_SUPER_NO_CLASS):        "super() used outside a class method.",
    _code_label(ErrorCode.E_STATIC_NO_CLASS):       "@staticmethod used outside a class.",
    # Semantic – collections
    _code_label(ErrorCode.E_HETEROGENEOUS_LIST):    "List literal contains elements of mixed types.  asmpython lists must be homogeneous.",
    _code_label(ErrorCode.E_HETEROGENEOUS_TUPLE):   "'in' on a tuple with mixed element types is not supported.",
    _code_label(ErrorCode.E_UNPACK_COUNT):          "Unpacking assignment count mismatch.",
    _code_label(ErrorCode.E_DICT_KEY_TYPE):         "Dict operation requires a str key.",
    _code_label(ErrorCode.E_SET_KEY_TYPE):          "Set elements must be strings in asmpython v1.",
    # Semantic – assembly
    _code_label(ErrorCode.E_ASM_OPERAND):           "Invalid assembly operand string.",
    _code_label(ErrorCode.E_ASM_REGISTER):          "Unrecognised x86-64 register name.",
    _code_label(ErrorCode.E_INCLUDE_ARG):           "include() argument must be a string literal package name.",
    # Semantic – misc
    _code_label(ErrorCode.E_ZIP_ARGS):              "zip() arguments must be lists or tuples.",
    _code_label(ErrorCode.E_ENUMERATE_ARG):         "enumerate() argument must be a list.",
    _code_label(ErrorCode.E_MATCH_PATTERN):         "Unsupported match/case pattern construct.",
    # Semantic – compiler extensions
    _code_label(ErrorCode.E_CONST_REASSIGNED):      "Cannot reassign a 'const' name.  Once declared, a const binding can never be rebound, augmented, deleted, or re-targeted.",
    _code_label(ErrorCode.E_CONST_REDEFINED):       "A 'const' name collides with a function or class of the same name.",
    _code_label(ErrorCode.E_PRIVATE_ACCESS_VIOLATION): "A '@private' member can only be accessed from inside its own class's methods.",
    _code_label(ErrorCode.E_PROTECTED_ACCESS_VIOLATION): "A '@protected' member can only be accessed from its own class or a subclass.",
    _code_label(ErrorCode.E_FINAL_CLASS_SUBCLASSED): "Cannot subclass a 'final class'.",
    _code_label(ErrorCode.E_FINAL_METHOD_OVERRIDDEN): "Cannot override a '@final' method in a subclass.",
    _code_label(ErrorCode.E_DECORATOR_WITHOUT_EXTENSION): "This decorator requires a compiler extension that was not activated with '--ext'.",
    _code_label(ErrorCode.E_SEALED_SUBCLASS_NOT_PERMITTED): "This class is not listed in its sealed parent's 'permits' list.",
    _code_label(ErrorCode.E_NONEXHAUSTIVE_MATCH): "'match' does not cover every case.  Add a trailing unguarded 'case _:' to handle the rest.",
    _code_label(ErrorCode.E_IMMUTABLE_FIELD_REASSIGNED): "Cannot assign to an '@immutable' field outside its class's __init__.",
    _code_label(ErrorCode.E_ENUM_REDEFINED): "An 'enum' name collides with a function or class of the same name.",
    _code_label(ErrorCode.E_ENUM_UNKNOWN_MEMBER): "No such member on this enum type.",
    _code_label(ErrorCode.E_ENUM_TYPE_MISMATCH): "Comparing members of two different enum types.",
    _code_label(ErrorCode.E_READONLY_PARAM_REASSIGNED): "Cannot reassign a parameter locked by '@readonly' or the 'const_params' extension.",
    _code_label(ErrorCode.E_READONLY_UNKNOWN_PARAM): "'@readonly' names a parameter that doesn't exist on this function.",
    _code_label(ErrorCode.E_UNDECLARED_GLOBAL_MUTATION): "Reassigning a module-level name from inside a function requires a 'global' declaration.",
    _code_label(ErrorCode.E_SHADOWED_GLOBAL): "This name shadows a module-level global (or, inside a nested function, a captured variable).",
    _code_label(ErrorCode.E_MUST_USE_DISCARDED): "The return value of this '@must_use' call is discarded.",
    _code_label(ErrorCode.E_IMPLICIT_ANY_PARAM): "This parameter has no annotation, default, or inferrable usage -- its type cannot be determined.",
    _code_label(ErrorCode.E_IMPLICIT_ANY_ASSIGN): "This assignment's value has no inferrable concrete type.",
    _code_label(ErrorCode.E_INTERFACE_REDEFINED): "An 'interface' name collides with a function or class of the same name.",
    _code_label(ErrorCode.E_INTERFACE_UNKNOWN): "'interface=' names an interface that was never declared.",
    _code_label(ErrorCode.E_INTERFACE_METHOD_MISSING): "This class does not implement a method required by its interface.",
    _code_label(ErrorCode.E_INTERFACE_METHOD_MISMATCH): "This method's signature does not match its interface's stub.",
    _code_label(ErrorCode.E_OVERLOAD_AMBIGUOUS): "This call matches two or more '@overload' signatures equally well.",
    _code_label(ErrorCode.E_OVERLOAD_NO_MATCH): "This call matches no '@overload' signature.",
    _code_label(ErrorCode.E_OVERLOAD_INCOMPATIBLE): "Two '@overload' signatures are indistinguishable from each other.",
    # Semantic – batch 2 (name resolution / redefinition)
    _code_label(ErrorCode.E_CLASS_NAME_COLLISION): "A class name collides with an existing function, class, enum, or builtin name.",
    _code_label(ErrorCode.E_INHERITANCE_CYCLE): "A class's base-class chain cycles back to itself.",
    # Semantic – function/method shape
    _code_label(ErrorCode.E_MISSING_SELF_PARAM): "An instance method's first parameter must be named 'self'.",
    _code_label(ErrorCode.E_DUNDER_SIGNATURE): "A dunder method (e.g. __eq__, __add__) must take exactly (self, other).",
    _code_label(ErrorCode.E_METHOD_NEEDS_INSTANCE): "This method needs an instance receiver; it cannot be called as a @staticmethod or @classmethod would be.",
    _code_label(ErrorCode.E_NO_METHOD): "The object's type has no method of this name.",
    # Semantic – unpacking / assignment
    _code_label(ErrorCode.E_STARRED_ASSIGN_NOT_LIST): "Starred assignment (e.g. '*x, = ...' or 'a, *b = ...') requires a list on the right-hand side.",
    _code_label(ErrorCode.E_TUPLE_ASSIGN_MIXED_TARGETS): "Tuple assignment mixing subscript/attribute targets requires the parallel form (one value per target).",
    _code_label(ErrorCode.E_UNPACK_TARGET_COUNT): "The number of unpacking targets doesn't match the number of values produced.",
    _code_label(ErrorCode.E_UNPACK_NOT_ITERABLE): "Cannot unpack a non-iterable object.",
    # Semantic – attribute / property / context manager
    _code_label(ErrorCode.E_PROPERTY_NO_SETTER): "Cannot assign to a property that has no setter.",
    _code_label(ErrorCode.E_NOT_A_CONTEXT_MANAGER): "An object used in a 'with' statement does not support the context manager protocol (missing __enter__/__exit__).",
    _code_label(ErrorCode.E_MODULE_NO_CALLABLE): "A module has no callable of this name.",
    # Semantic – match/case patterns
    _code_label(ErrorCode.E_OR_PATTERN_CAPTURE): "A capture pattern cannot appear inside an or-pattern.",
    _code_label(ErrorCode.E_MATCH_ARGS_MISSING): "A class used with a positional match pattern must define __match_args__.",
    _code_label(ErrorCode.E_MATCH_ARGS_TOO_MANY): "Too many positional patterns for this class's __match_args__.",
    # Semantic – containment / comparison
    _code_label(ErrorCode.E_CONTAINS_TYPE_MISMATCH): "The 'in'/'not in' needle's type doesn't match the container's element type.",
    _code_label(ErrorCode.E_NO_CONTAINS_METHOD): "A class used with 'in'/'not in' must define __contains__.",
    _code_label(ErrorCode.E_STRING_COMPARISON_OP): "This comparison operator is not supported between strings.",
    _code_label(ErrorCode.E_UNCOMPARABLE_TYPES): "These two types cannot be compared with this operator.",
    # Semantic – container literals / spreads
    _code_label(ErrorCode.E_SPREAD_NOT_ITERABLE): "List unpacking ('*expr' inside a list literal) requires a list or tuple.",
    _code_label(ErrorCode.E_DICT_UNPACK_TYPE): "Dict unpacking ('**expr' inside a dict literal) requires a dict.",
    # Semantic – indexing
    _code_label(ErrorCode.E_TUPLE_INDEX_RANGE): "A constant tuple index is out of range for this tuple's length.",
    _code_label(ErrorCode.E_TUPLE_INDEX_NOT_CONST): "Indexing a heterogeneous tuple requires a compile-time constant index.",
    # Semantic – method/function call errors on builtins
    _code_label(ErrorCode.E_LIST_ELEMENT_TYPE_UNSUPPORTED): "This value's type is not supported as a list element.",
    _code_label(ErrorCode.E_BAD_FORMAT_STRING): "A '%' format string contains an invalid or unsupported conversion specifier.",
    _code_label(ErrorCode.E_SORT_KEY_ARITY): "A sort key= function must take exactly one argument.",
    _code_label(ErrorCode.E_SORT_KEY_TYPE): "sort()'s key= argument must be a lambda literal or a name bound to one.",
    _code_label(ErrorCode.E_FORMAT_UNKNOWN_FIELD): "str.format() references a field name that has no matching keyword argument.",
    _code_label(ErrorCode.E_FORMAT_INDEX_RANGE): "str.format() references a positional field index that is out of range.",
    _code_label(ErrorCode.E_DSTAR_NOT_DICT): "A '**expr' call argument must evaluate to a dict.",
    _code_label(ErrorCode.E_KEY_UNSUPPORTED_FORM): "key= is only supported for the single-iterable call form.",
    _code_label(ErrorCode.E_DSTAR_NO_PARAM_LIST): "'**expr' expansion requires a statically known parameter list, which this callee doesn't have.",
    # Semantic – batch 3
    _code_label(ErrorCode.E_MLANG_INVALID_ARG): "An mlang Code(...)/Sig(...) argument has an invalid shape (wrong literal kind, arity, or structure).",
    _code_label(ErrorCode.E_MLANG_COMPILE_FAILED): "The source passed to mlang Code(...) failed to compile.",
    _code_label(ErrorCode.E_BUILTIN_REDEFINED): "A top-level function or class redefines a builtin name.",
    _code_label(ErrorCode.E_TUPLE_ASSIGN_VALUE_TYPE): "A value in a parallel tuple-assignment has a type that isn't supported for this form.",
    _code_label(ErrorCode.E_INTERNAL_UNHANDLED_NODE): "Internal: the compiler encountered an AST node kind it does not handle here. This indicates a compiler bug, not a source error.",
    _code_label(ErrorCode.E_TUPLE_ELEMENT_TYPE_UNSUPPORTED): "A tuple literal element has a type that is not yet supported.",
    _code_label(ErrorCode.E_DICT_VALUE_TYPE_UNSUPPORTED): "A dict value has a type that is not yet supported.",
    _code_label(ErrorCode.E_DICT_VALUE_TYPE_MIXED): "A dict literal mixes value types that can't share a dict (e.g. float with a pointer-typed value).",
    _code_label(ErrorCode.E_INTERPRETER_ONLY_FEATURE): "This call requires a Python interpreter and cannot be compiled to native code.",
    _code_label(ErrorCode.E_MLANG_UNKNOWN_EXPORT): "mlang Code(...) has no export of this name.",
    _code_label(ErrorCode.E_RSPLIT_MAXSPLIT): "str.rsplit()'s maxsplit argument must be the literal 1; only the last-separator split is implemented.",
    _code_label(ErrorCode.E_FORMAT_FIELD_UNSUPPORTED): "str.format() field specifier uses a form that is not supported (e.g. attribute/index access inside the field).",
}


# ---------------------------------------------------------------------------
# CPython exception-name mapping
# ---------------------------------------------------------------------------
# Where a diagnostic corresponds to a real CPython runtime exception, the
# formatted message leads with that exception's class name (e.g.
# "NameError: name 'greet' is not defined") instead of a generic
# "semantic error: [E001] ..." tag, so users coming from CPython recognise
# the failure immediately. The E0xx/P0xx/L0xx code is still appended in
# brackets for `--explain` lookups.
#
# Lex/parse errors are all reported as SyntaxError, matching how CPython
# itself surfaces tokenizer/parser problems. A few codes have no real
# CPython equivalent (redefinition is legal in CPython -- it just silently
# rebinds; asm-directive and const-extension diagnostics are asmpython-only
# concepts) and are intentionally left unmapped, falling back to the
# original "{phase} error" wording.
_PYTHON_EXCEPTION_NAME: dict[int, str] = {
    # Name resolution
    ErrorCode.E_UNDEFINED_NAME: "NameError",
    ErrorCode.E_UNDEFINED_FUNC: "NameError",
    ErrorCode.E_NO_SUCH_MODULE: "ModuleNotFoundError",
    # Types
    ErrorCode.E_TYPE_MISMATCH: "TypeError",
    ErrorCode.E_BINARY_OP_TYPE: "TypeError",
    ErrorCode.E_UNARY_OP_TYPE: "TypeError",
    ErrorCode.E_FSTRING_SEGMENT_TYPE: "TypeError",
    ErrorCode.E_RETURN_TYPE: "TypeError",
    ErrorCode.E_INDEX_TYPE: "TypeError",
    ErrorCode.E_INDEX_OBJECT_TYPE: "TypeError",
    ErrorCode.E_ITER_TYPE: "TypeError",
    ErrorCode.E_ASSIGN_TYPE: "TypeError",
    # Calls
    ErrorCode.E_ARG_COUNT: "TypeError",
    ErrorCode.E_ARG_TYPE: "TypeError",
    ErrorCode.E_NOT_CALLABLE: "TypeError",
    ErrorCode.E_FORMAT_ARG_COUNT: "TypeError",
    ErrorCode.E_FORMAT_ARG_TYPE: "TypeError",
    ErrorCode.E_FORMAT_LITERAL: "TypeError",
    # Control flow (CPython reports these at parse time)
    ErrorCode.E_BREAK_OUTSIDE_LOOP: "SyntaxError",
    ErrorCode.E_CONTINUE_OUTSIDE_LOOP: "SyntaxError",
    ErrorCode.E_RETURN_OUTSIDE_FUNC: "SyntaxError",
    # Class / attribute
    ErrorCode.E_NO_ATTR: "AttributeError",
    ErrorCode.E_NOT_AN_EXCEPTION: "TypeError",
    ErrorCode.E_INDEX_ASSIGN: "TypeError",
    ErrorCode.E_SUPER_NO_CLASS: "RuntimeError",
    ErrorCode.E_STATIC_NO_CLASS: "SyntaxError",
    # Collections
    ErrorCode.E_HETEROGENEOUS_LIST: "TypeError",
    ErrorCode.E_HETEROGENEOUS_TUPLE: "TypeError",
    ErrorCode.E_UNPACK_COUNT: "ValueError",
    ErrorCode.E_DICT_KEY_TYPE: "TypeError",
    ErrorCode.E_SET_KEY_TYPE: "TypeError",
    # Misc
    ErrorCode.E_ZIP_ARGS: "TypeError",
    ErrorCode.E_ENUMERATE_ARG: "TypeError",
    ErrorCode.E_MATCH_PATTERN: "SyntaxError",
    # Name resolution / redefinition (batch 2)
    ErrorCode.E_CLASS_NAME_COLLISION: "TypeError",
    ErrorCode.E_INHERITANCE_CYCLE: "TypeError",
    # Function/method shape
    ErrorCode.E_MISSING_SELF_PARAM: "TypeError",
    ErrorCode.E_DUNDER_SIGNATURE: "TypeError",
    ErrorCode.E_METHOD_NEEDS_INSTANCE: "TypeError",
    ErrorCode.E_NO_METHOD: "AttributeError",
    # Unpacking / assignment
    ErrorCode.E_STARRED_ASSIGN_NOT_LIST: "TypeError",
    ErrorCode.E_TUPLE_ASSIGN_MIXED_TARGETS: "SyntaxError",
    ErrorCode.E_UNPACK_TARGET_COUNT: "ValueError",
    ErrorCode.E_UNPACK_NOT_ITERABLE: "TypeError",
    # Attribute / property / context manager
    ErrorCode.E_PROPERTY_NO_SETTER: "AttributeError",
    ErrorCode.E_NOT_A_CONTEXT_MANAGER: "AttributeError",
    ErrorCode.E_MODULE_NO_CALLABLE: "AttributeError",
    # Match/case patterns
    ErrorCode.E_OR_PATTERN_CAPTURE: "SyntaxError",
    ErrorCode.E_MATCH_ARGS_MISSING: "TypeError",
    ErrorCode.E_MATCH_ARGS_TOO_MANY: "TypeError",
    # Containment / comparison
    ErrorCode.E_CONTAINS_TYPE_MISMATCH: "TypeError",
    ErrorCode.E_NO_CONTAINS_METHOD: "TypeError",
    ErrorCode.E_STRING_COMPARISON_OP: "TypeError",
    ErrorCode.E_UNCOMPARABLE_TYPES: "TypeError",
    # Container literals / spreads
    ErrorCode.E_SPREAD_NOT_ITERABLE: "TypeError",
    ErrorCode.E_DICT_UNPACK_TYPE: "TypeError",
    # Indexing
    ErrorCode.E_TUPLE_INDEX_RANGE: "IndexError",
    ErrorCode.E_TUPLE_INDEX_NOT_CONST: "TypeError",
    # Method/function call errors on builtins
    ErrorCode.E_LIST_ELEMENT_TYPE_UNSUPPORTED: "TypeError",
    ErrorCode.E_BAD_FORMAT_STRING: "ValueError",
    ErrorCode.E_SORT_KEY_ARITY: "TypeError",
    ErrorCode.E_SORT_KEY_TYPE: "TypeError",
    ErrorCode.E_FORMAT_UNKNOWN_FIELD: "KeyError",
    ErrorCode.E_FORMAT_INDEX_RANGE: "IndexError",
    ErrorCode.E_DSTAR_NOT_DICT: "TypeError",
    ErrorCode.E_KEY_UNSUPPORTED_FORM: "TypeError",
    ErrorCode.E_DSTAR_NO_PARAM_LIST: "TypeError",
    # Batch 3
    ErrorCode.E_MLANG_INVALID_ARG: "TypeError",
    ErrorCode.E_MLANG_COMPILE_FAILED: "RuntimeError",
    ErrorCode.E_BUILTIN_REDEFINED: "TypeError",
    ErrorCode.E_TUPLE_ASSIGN_VALUE_TYPE: "TypeError",
    # E_INTERNAL_UNHANDLED_NODE intentionally unmapped: a compiler-internal
    # invariant violation, not a real CPython exception a user's source
    # would ever raise.
    ErrorCode.E_TUPLE_ELEMENT_TYPE_UNSUPPORTED: "TypeError",
    ErrorCode.E_DICT_VALUE_TYPE_UNSUPPORTED: "TypeError",
    ErrorCode.E_DICT_VALUE_TYPE_MIXED: "TypeError",
    ErrorCode.E_INTERPRETER_ONLY_FEATURE: "RuntimeError",
    ErrorCode.E_MLANG_UNKNOWN_EXPORT: "AttributeError",
    ErrorCode.E_RSPLIT_MAXSPLIT: "ValueError",
    ErrorCode.E_FORMAT_FIELD_UNSUPPORTED: "ValueError",
}


def _python_exception_name(code: Optional[int]) -> Optional[str]:
    """Return the CPython exception class name for a code, if one applies.

    Lex/parse-phase codes (< 200) always map to SyntaxError, mirroring
    CPython's own tokenizer/parser diagnostics. Semantic codes are looked
    up individually since they span many different real exception types.
    """
    if code is None:
        return None
    if code < 200:
        return "SyntaxError"
    return _PYTHON_EXCEPTION_NAME.get(code)


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
        exc_name = _python_exception_name(self.code)
        if exc_name is not None:
            kind = f"{exc_name}: {code_tag}"
        else:
            kind = f"{self.phase} error: {code_tag}"
        if self.pos is not None:
            out.append(f"{filename}:{self.pos.line}:{self.pos.col}: {kind}{self.message}")
            line_text = _get_line(source, self.pos.line)
            if line_text is not None:
                out.append("  " + line_text.rstrip("\n"))
                out.append("  " + " " * (self.pos.col - 1) + "^")
        else:
            out.append(f"{filename}: {kind}{self.message}")
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

    `errors` is a non-empty list of SemaError instances in source order
    UNDER A PYTHON-HOSTED COMPILER. When asmpython compiles itself (this
    file is part of the self-compiled compiler source), `raise
    SemaError(message, pos, code)` does not construct a real SemaError
    instance at runtime at all -- asmpython's native exception model only
    carries a message string and a type id through `_runtime_raise`, so
    `except SemaError as e: self._collected_errors.append(e)` (sema.py's
    _try_check_block) only ever has a plain string for `e`, never a real
    object with `.message`/`.pos`/`.code`. `errors` is therefore typed
    loosely here (not `list[SemaError]`) since it holds plain strings when
    self-compiled and real SemaError instances when Python-hosted; format_all
    handles both.
    The first error is also available as `errors[0]` for callers that want
    to surface a single representative diagnostic.
    """

    def __init__(self, errors: list) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} semantic error(s)")

    def format_all(self, src: str, filename: str) -> str:
        """Format every collected error, separated by blank lines.

        Under a Python-hosted compiler, each entry in `self.errors` is a
        real SemaError instance and `.format(...)` gives the full
        `file:line:col: ... + source line + caret` presentation. Under a
        self-compiled (selfhosted) compiler, `raise SemaError(...)` never
        constructs a real instance at runtime (asmpython's native
        exception model only carries a message string through
        `_runtime_raise`), so each entry is just that plain string instead
        -- printed as-is, without the file/line/caret decoration that
        genuinely isn't available in that case.
        """
        parts: list[str] = []
        for e in self.errors:
            if isinstance(e, str):
                parts.append(e)
            else:
                # Explicit cast: under a self-compiled binary, `for e in
                # self.errors` types e as opaque int (list element without
                # element annotation), so e.format(...) reports "int has no
                # method 'format'". Casting to CompileError gives gen1's sema
                # the type it needs to resolve the inherited format() method.
                _ce: CompileError = e
                parts.append(_ce.format(src, filename))
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
    if prefix not in ("L", "P", "E"):
        return ""
    norm_label = f"{prefix}{n:03d}"
    desc = ERROR_DESCRIPTIONS.get(norm_label)
    if desc is None:
        return ""
    return f"[{norm_label}] {desc}"


def _get_line(source: str, lineno: int) -> Optional[str]:
    lines = source.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return None
