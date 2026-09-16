"""A collection of string constants and two string-processing classes.

COVERAGE: the constants `ascii_lowercase`, `ascii_uppercase`, `ascii_letters`,
`digits`, `hexdigits`, `octdigits`, `punctuation`, `printable`, `whitespace`
-- CPython's own characters, in CPython's own order, since both are
observable (`string.digits[0]` and `"".join(sorted(string.digits))` are not
the same question); `capwords`; `Template` (PEP 292 `$identifier`,
`${identifier}` and `$$`, `.substitute`, `.safe_substitute`, `.template`,
`.is_valid`, `.get_identifiers`, subclassing through `delimiter`, `idpattern`,
`braceidpattern` and `flags`); `Formatter` (the class behind `str.format`,
with every hook a subclass overrides: `.format`, `.vformat`, `.parse`,
`.get_field`, `.get_value`, `.check_unused_args`, `.format_field`,
`.convert_field`).

NOT COVERED, and refused BY NAME rather than silently misformatted:

* `Template.pattern` as a live compiled-regex OBJECT. CPython exposes one
  (through a lazily-computed descriptor, so a subclass with a different
  `idpattern` gets its own). This module recomputes the same pattern text on
  every call instead of caching it on the class -- a COST difference, like
  `deque`'s O(n) ends in `collections`, and not an answer difference, because
  `re.compile` is deterministic over the same text and flags. `Template()`
  never depends on the compiled object's identity, so nothing observable
  changes; a program that reaches into `.pattern` and compares it `is` a
  previous read would see a different object each time, which CPython does
  not, and that one case is refused: `.pattern` raises `AttributeError`
  rather than returning something that looks right and is not.
* `Formatter.parse`'s error text for a MALFORMED field (an empty conversion,
  a conversion longer than one character with no following `:`, an unmatched
  brace deep inside a nested spec) is not guaranteed to read identically to
  CPython's C parser -- the SHAPE of the error (a `ValueError`, at the same
  place) matches; the exact wording in the more obscure corners may not.
* Format specs nested more than one level (`{:{:{}}}`) raise `ValueError`,
  which is CPython's own limit (`_vformat`'s recursion counter starts at 2),
  not a narrower one this module adds.

## Why `Formatter` is not just `str.format` wearing a class

`str.format` itself (`apy_str_format` at the runtime boundary) already
implements the whole replacement-field mini-language, and `format(value,
spec)` (`apy_format`) already implements the type-and-alignment mini-language
inside a spec -- both are exercised daily by ordinary f-strings and
`"...".format(...)` calls, so this module uses `format()` for
`Formatter.format_field` rather than reimplementing the spec language a
second time.

But `Formatter` is not a thin wrapper around `str.format`: its entire
published reason to exist is that `get_value`, `get_field`, `format_field`
and `convert_field` are OVERRIDABLE, and a subclass overriding one of them
has to see it actually called for every field `.format()` processes. Handing
the whole format string to the runtime's `apy_str_format` would answer
`str.format` correctly and ignore every override -- an "accepted and
ignored" shape this project's own `docs/STDLIB.md` names as worse than a
refusal. So `parse` (turning the string into `(literal_text, field_name,
format_spec, conversion)` tuples) and the field-name splitting inside
`get_field` (turning `"0.real"` into `(0, [(True, "real")])`) are written out
in plain Python here, and `_vformat` walks them exactly the way CPython's own
(pure-Python-since-`_string.formatter_parser`-is-C-only-for-speed) algorithm
does, calling back through `self.get_value`, `self.get_field`,
`self.convert_field` and `self.format_field` at every step -- so a subclass
overriding any one of them changes what `.format()` produces, which is the
whole point of the class.
"""
import re

# ---------------------------------------------------------------------------
# Constants. CPython's own characters, in CPython's own order -- checked
# against a running CPython 3.14 rather than typed from memory, because the
# ORDER is observable (`string.hexdigits` is digits then lowercase then
# uppercase, not sorted) and so is which punctuation marks are and are not
# in the set (no space, no non-ASCII quote characters).
# ---------------------------------------------------------------------------

ascii_lowercase = 'abcdefghijklmnopqrstuvwxyz'
ascii_uppercase = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
ascii_letters = ascii_lowercase + ascii_uppercase
digits = '0123456789'
hexdigits = digits + 'abcdef' + 'ABCDEF'
octdigits = '01234567'
punctuation = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
whitespace = ' \t\n\r\x0b\x0c'
printable = digits + ascii_letters + punctuation + whitespace


def capwords(s, sep=None):
    """Split on `sep` (runs of whitespace if None, as `str.split` does),
    capitalize each piece, and rejoin on a single space (or `sep`).

    NOT `s.title()`: `title()` capitalizes after every non-letter, so
    `"jo's bike".title()` answers `"Jo'S Bike"`. `capwords` only touches the
    first character of each SPLIT piece, so the apostrophe does not start a
    new word.
    """
    pieces = s.split(sep)
    capped = []
    for piece in pieces:
        capped.append(piece.capitalize())
    joiner = sep if sep is not None else ' '
    return joiner.join(capped)


# ---------------------------------------------------------------------------
# Template -- PEP 292 `$`-substitution.
# ---------------------------------------------------------------------------

#: Distinct from every real mapping (including an empty dict), so
#: `substitute()` with no positional argument can tell "nothing was passed"
#: from "an empty mapping was passed" -- the same trick CPython's own
#: `_sentinel_dict` plays, and needed for the same reason: `mapping` is
#: POSITIONAL-ONLY, so `t.substitute(mapping="x")` has to mean "substitute
#: the key `mapping`", not "fill the `mapping` parameter by name" -- and it
#: does, because a positional-only parameter cannot be filled by keyword at
#: all, so that call's `mapping="x"` lands in `**kws` instead.
_UNSET = []


def _merge(mapping, kws):
    """`kws` OVERRIDES `mapping`, which is what makes
    `Template("$x").substitute({"x": 1}, x=2)` answer `"2"`."""
    if not kws:
        return mapping
    merged = {}
    for key in mapping:
        merged[key] = mapping[key]
    for key in kws:
        merged[key] = kws[key]
    return merged


class Template:
    """A string with `$name`, `${name}` and `$$` placeholders.

    `$name` MATCHES THE LONGEST IDENTIFIER IT CAN, so `"$foobar"` reads as
    one placeholder named `foobar` and never as `$foo` followed by the
    letters `bar` -- use `${foo}bar` to get that. `$$` is a literal `$`, not
    the start of a placeholder named `$`.
    """

    delimiter = '$'
    #: `(?a:...)` restricts `\\w`-shaped classes to ASCII even under a
    #: Unicode-matching engine, which is CPython's own reason for writing it
    #: this way rather than `[A-Za-z_][A-Za-z0-9_]*` -- kept for the same
    #: documented reason, not because it changes an answer here.
    idpattern = r'(?a:[_a-z][_a-z0-9]*)'
    braceidpattern = None
    flags = None

    def __init__(self, template):
        self.template = template

    def _compiled(self):
        """Recomputed on every call rather than cached on the class -- a
        COST difference from CPython's descriptor, not an answer one. See
        the module docstring for what that means `Template.pattern` cannot
        promise here."""
        cls = type(self)
        delim = re.escape(cls.delimiter)
        idp = cls.idpattern
        bidp = cls.braceidpattern if cls.braceidpattern is not None \
            else cls.idpattern
        use_flags = cls.flags if cls.flags is not None else re.IGNORECASE
        pattern = (delim + r"(?:(?P<escaped>" + delim + r")|(?P<named>"
                  + idp + r")|\{(?P<braced>" + bidp + r")\}|(?P<invalid>))")
        return re.compile(pattern, use_flags)

    def _invalid(self, mo):
        i = mo.start('invalid')
        lines = self.template[:i].splitlines(keepends=True)
        if not lines:
            lineno = 1
            colno = 1
        else:
            colno = i - len(''.join(lines[:-1]))
            lineno = len(lines)
        raise ValueError('Invalid placeholder in string: line '
                         + str(lineno) + ', col ' + str(colno))

    def substitute(self, mapping=_UNSET, **kws):
        effective = kws if mapping is _UNSET else _merge(mapping, kws)
        compiled = self._compiled()

        def convert(mo):
            named = mo.group('named')
            if named is None:
                named = mo.group('braced')
            if named is not None:
                return str(effective[named])
            if mo.group('escaped') is not None:
                return self.delimiter
            self._invalid(mo)
            return ''

        return compiled.sub(convert, self.template)

    def safe_substitute(self, mapping=_UNSET, **kws):
        effective = kws if mapping is _UNSET else _merge(mapping, kws)
        compiled = self._compiled()

        def convert(mo):
            named = mo.group('named')
            if named is None:
                named = mo.group('braced')
            if named is not None:
                if named in effective:
                    return str(effective[named])
                return mo.group()
            if mo.group('escaped') is not None:
                return self.delimiter
            if mo.group('invalid') is not None:
                return mo.group()
            raise ValueError('Unrecognized named group in pattern')

        return compiled.sub(convert, self.template)

    def is_valid(self):
        compiled = self._compiled()
        for mo in compiled.finditer(self.template):
            if mo.group('invalid') is not None:
                return False
            if (mo.group('named') is None and mo.group('braced') is None
                    and mo.group('escaped') is None):
                raise ValueError('Unrecognized named group in pattern')
        return True

    def get_identifiers(self):
        compiled = self._compiled()
        ids = []
        for mo in compiled.finditer(self.template):
            named = mo.group('named')
            if named is None:
                named = mo.group('braced')
            if named is not None:
                if named not in ids:
                    ids.append(named)
            elif (mo.group('invalid') is None
                  and mo.group('escaped') is None):
                raise ValueError('Unrecognized named group in pattern')
        return ids


# ---------------------------------------------------------------------------
# Formatter -- the class behind `str.format`. See the module docstring for
# why this is a real reimplementation of PEP 3101's algorithm and not a call
# out to `str.format`.
# ---------------------------------------------------------------------------

def _all_ascii_digits(s):
    if not s:
        return False
    for ch in s:
        if ch < '0' or ch > '9':
            return False
    return True


def _split_field_name(field_name):
    """`"0.real"` -> `(0, [(True, "real")])`; `"x[0][k]"` -> `("x", [(False,
    0), (False, "k")])`.

    THE FIRST PIECE, up to the first `.` or `[`, is the argument key: an
    integer if it is all digits (positional), the bare text otherwise
    (keyword). EVERY STEP AFTER IT is `.name` (an attribute) or `[key]` (an
    item -- and an all-digit key inside brackets is an INT, so `{0[1]}`
    indexes a list rather than looking up the string key `"1"`, exactly as
    CPython's own field-name splitter reads it).
    """
    n = len(field_name)
    first_end = n
    i = 0
    while i < n:
        ch = field_name[i]
        if ch == '.' or ch == '[':
            first_end = i
            break
        i += 1
    first = field_name[:first_end]
    if _all_ascii_digits(first):
        first = int(first)
    rest = []
    i = first_end
    while i < n:
        ch = field_name[i]
        if ch == '.':
            i += 1
            start = i
            while i < n and field_name[i] != '.' and field_name[i] != '[':
                i += 1
            name = field_name[start:i]
            if not name:
                raise ValueError("Empty attribute in format string")
            rest.append((True, name))
        elif ch == '[':
            i += 1
            start = i
            while i < n and field_name[i] != ']':
                i += 1
            if i >= n:
                raise ValueError("Missing ']' in format string")
            key = field_name[start:i]
            i += 1
            if _all_ascii_digits(key):
                key = int(key)
            rest.append((False, key))
        else:
            raise ValueError(
                "Only '.' or '[' may follow ']' in format field specifier")
    return first, rest


def _parse_format_string(format_string):
    """`(literal_text, field_name, format_spec, conversion)` tuples -- see
    `Formatter.parse`.

    `{{` AND `}}` FLUSH IMMEDIATELY: the accumulated literal text plus one
    literal brace becomes its own tuple (`field_name` `None`) the moment the
    doubled brace is seen, rather than being merged into whatever literal
    text comes after it. That is an observable CPython quirk and not a
    simplification here -- `"{{a}}".format()` and `Formatter().parse` both
    show it, and the differential test checks it: `parse("{{}}")` answers
    TWO tuples, `('{', None, None, None)` and `('}', None, None, None)`, not
    one `('{}', None, None, None)`.

    A NESTED SPEC (`{:{width}}`) is captured VERBATIM, braces included, by
    tracking `{`/`}` depth while scanning for the field's own closing brace
    -- one level, which is what `_vformat` below enforces by recursion depth
    and is CPython's own limit too.
    """
    out = []
    literal = []
    i = 0
    n = len(format_string)
    while i < n:
        ch = format_string[i]
        if ch == '{':
            if i + 1 < n and format_string[i + 1] == '{':
                literal.append('{')
                out.append((''.join(literal), None, None, None))
                literal = []
                i += 2
                continue
            i += 1
            start = i
            depth = 0
            colon = -1
            bang = -1
            while i < n and (format_string[i] != '}' or depth):
                c = format_string[i]
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                elif c == ':' and colon < 0 and depth == 0:
                    colon = i
                elif c == '!' and bang < 0 and colon < 0 and depth == 0:
                    bang = i
                i += 1
            if i >= n:
                raise ValueError("Single '{' encountered in format string")
            # THE FIELD NAME ENDS AT WHICHEVER OF `!`/`:` COMES FIRST -- `!`
            # always precedes `:` when both are present, since the grammar
            # is `field!conversion:spec`.
            if bang >= 0:
                field_end = bang
            elif colon >= 0:
                field_end = colon
            else:
                field_end = i
            field_name = format_string[start:field_end]
            conversion = None
            if bang >= 0:
                conv_end = colon if colon >= 0 else i
                conversion = format_string[bang + 1:conv_end]
                if not conversion:
                    raise ValueError(
                        "end of string while looking for conversion "
                        "specifier")
                if len(conversion) != 1:
                    raise ValueError(
                        "expected ':' after conversion specifier")
            format_spec = format_string[colon + 1:i] if colon >= 0 else ''
            out.append((''.join(literal), field_name, format_spec,
                       conversion))
            literal = []
            i += 1
            continue
        if ch == '}':
            if i + 1 < n and format_string[i + 1] == '}':
                literal.append('}')
                out.append((''.join(literal), None, None, None))
                literal = []
                i += 2
                continue
            raise ValueError("Single '}' encountered in format string")
        literal.append(ch)
        i += 1
    if literal:
        out.append((''.join(literal), None, None, None))
    return out


class Formatter:
    """The class `str.format` is specified in terms of (PEP 3101), with
    every step overridable. See the module docstring for why this matters:
    a subclass overriding `get_value` or `format_field` has to see that
    override actually take effect."""

    def format(self, format_string, *args, **kwargs):
        return self.vformat(format_string, args, kwargs)

    def vformat(self, format_string, args, kwargs):
        used_args = set()
        result, _ = self._vformat(format_string, args, kwargs, used_args, 2)
        self.check_unused_args(used_args, args, kwargs)
        return result

    def _vformat(self, format_string, args, kwargs, used_args,
                 recursion_depth, auto_arg_index=0):
        if recursion_depth < 0:
            raise ValueError('Max string recursion exceeded')
        result = []
        for literal_text, field_name, format_spec, conversion \
                in self.parse(format_string):
            if literal_text:
                result.append(literal_text)
            if field_name is not None:
                first, _rest = _split_field_name(field_name)
                if first == '':
                    if auto_arg_index is False:
                        raise ValueError(
                            'cannot switch from manual field specification '
                            'to automatic field numbering')
                    field_name = str(auto_arg_index) + field_name
                    auto_arg_index += 1
                elif isinstance(first, int):
                    if auto_arg_index:
                        raise ValueError(
                            'cannot switch from automatic field numbering '
                            'to manual field specification')
                    auto_arg_index = False

                obj, arg_used = self.get_field(field_name, args, kwargs)
                used_args.add(arg_used)
                obj = self.convert_field(obj, conversion)
                format_spec, auto_arg_index = self._vformat(
                    format_spec, args, kwargs, used_args,
                    recursion_depth - 1, auto_arg_index)
                result.append(self.format_field(obj, format_spec))
        return ''.join(result), auto_arg_index

    def get_value(self, key, args, kwargs):
        if isinstance(key, int):
            return args[key]
        return kwargs[key]

    def check_unused_args(self, used_args, args, kwargs):
        pass

    def format_field(self, value, format_spec):
        return format(value, format_spec)

    def convert_field(self, value, conversion):
        if conversion is None:
            return value
        if conversion == 's':
            return str(value)
        if conversion == 'r':
            return repr(value)
        if conversion == 'a':
            return ascii(value)
        raise ValueError("Unknown conversion specifier "
                         + str(conversion))

    def parse(self, format_string):
        return _parse_format_string(format_string)

    def get_field(self, field_name, args, kwargs):
        first, rest = _split_field_name(field_name)
        obj = self.get_value(first, args, kwargs)
        for is_attr, key in rest:
            if is_attr:
                obj = getattr(obj, key)
            else:
                obj = obj[key]
        return obj, first
