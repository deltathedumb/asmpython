"""`textwrap`, as ordinary Python this compiler compiles.

COVERAGE: `TextWrapper` with every real constructor parameter -- `width`,
`initial_indent`, `subsequent_indent`, `expand_tabs`, `replace_whitespace`,
`fix_sentence_endings`, `break_long_words`, `drop_whitespace`,
`break_on_hyphens`, `tabsize`, `max_lines`, `placeholder` -- and its `wrap`/
`fill` methods; the module functions `wrap`, `fill`, `shorten`, `dedent`,
`indent`.

NOT COVERED: locale- or charset-aware sentence detection (`fix_sentence_endings`
is CPython's own ASCII-lowercase heuristic, imperfect there too); the exact
`TypeError` CPython's `dedent` raises for a non-`str` argument, naming the
type -- a non-`str` here raises whatever the first `str` operation raises,
which is a real but differently-worded `TypeError`.

## Why this is not `wordsep_re.split`

CPython's real splitter is one regex, `wordsep_re`, and it is not usable
here: it depends on lookbehind (`(?<=...)`) in three places, and
`bundled/re.py`'s own COVERAGE line says so -- lookbehind is refused BY NAME
there. Reaching for it would either silently do the wrong thing or refuse a
program that imports nothing but `textwrap`.

So `_wordsep_split` below is a hand-written scanner rather than a regex, but
it is not a *different* algorithm -- it is `wordsep_re` read as what it
actually decides, character by character, and reproduced without an engine.
That regex has exactly three top-level alternatives, tried left to right at
every position: a run of whitespace; a bare run of two-or-more hyphens
standing between two words (an em-dash, `--`); or a "word" scanned lazily one
character at a time until one of three things ends it -- whitespace or the
end of text, a hyphen CPython's grammar treats as a breakpoint, or the start
of a qualifying em-dash run. Every one of those decisions turns out to be
answerable from a FIXED, SMALL window of nearby characters -- never more than
two before a hyphen for one lookbehind form, or three for the other, and
never more than two after -- which is exactly why a lookbehind-free scan can
reproduce it: nothing here needs unbounded backtracking, only a few
characters of context around each hyphen. Verified against a real CPython
3.14 by working out each of the three alternatives' fixed-width conditions
from the regex source and then checking the result char-for-char on the
hyphenation and em-dash cases in `tests/stdlib/textwrap.py` -- ordinary
words, `well-known`-style compounds that break after the hyphen, `x-ray`-
style compounds that do not, digit-led compounds like `23-year-old` (a digit
is `\\w` but not CPython's "letter" class `[^\\d\\W]`, so the hyphen right
after one never breaks), `it's-a-test`-style words where an apostrophe sits
in the lookbehind window, and `--`/`---` runs both between words and at the
edges of the text.

**The first draft of the second lookbehind form was off by one, and a
30-string curated case list did not catch it.** `(?<=letter-letter-)` reads
as a 4-character window ending AT the hyphen being tested (three characters
before it, the hyphen itself as the fourth) -- the draft instead checked
four characters strictly BEFORE the hyphen, one position too far back. Every
hand-picked test happened to have its second hyphen preceded by two LETTERS
(`well-known`, `co-op-er-ate`, `a-b-c-d`'s second hyphen), which the OTHER
lookbehind form (`letter{2}-`) already covers on its own, so the bug never
fired. A broader sweep over hundreds of generated hyphen/apostrophe/em-dash
strings at nine widths, plus real prose pulled from this file, found the one
case where only the buggy form applied: `"it's-a-test"`, where an apostrophe
sits where a letter would need to be for the FIRST form but not the SECOND.
CPython keeps `"it's-a-"` as one hyphenated chunk; the buggy scanner split it
one character early. Fixed by re-deriving the window from the regex's own
semantics (the lookbehind is anchored at the position AFTER the hyphen, not
at the hyphen itself) rather than from the failing case alone.

`wordsep_simple_re` (the `break_on_hyphens=False` path) needed none of this:
it only splits on whitespace runs, which `_wordsep_simple_split` does
directly.

`sentence_end_re` is likewise a small fixed check -- a lowercase ASCII
letter, then `. ! ?`, then an optional closing quote, at the end of the
chunk -- reproduced as `_looks_like_sentence_end` rather than a pattern.
"""

_whitespace = '\t\n\x0b\x0c\r '


def _is_ws(c):
    return c in _whitespace


def _is_word_char(c):
    # \w: alphanumeric or underscore. `isalnum` alone misses the underscore.
    return c == '_' or c.isalnum()


def _is_letter(c):
    # [^\d\W] -- a word character that is not a digit.
    return _is_word_char(c) and not c.isdigit()


def _is_word_punct(c):
    # word_punct = r'[\w!"\'&.,?]'
    return _is_word_char(c) or c in '!"\'&.,?'


def _any_non_hyphen(s):
    for c in s:
        if c != '-':
            return True
    return False


def _em_dash_run(text, i, n):
    """If `text[i]` starts a qualifying `-{2,}` run -- preceded by a
    word_punct character and followed, once the hyphens end, by a `\\w`
    character -- the index just past the run. Otherwise None.

    Used both as the top-level "bare em-dash" alternative and, inside a word
    scan, as the lookahead that ends the word right before one.
    """
    if text[i] != '-':
        return None
    if i == 0 or not _is_word_punct(text[i - 1]):
        return None
    j = i
    while j < n and text[j] == '-':
        j += 1
    if j - i < 2:
        return None
    if j >= n or not _is_word_char(text[j]):
        return None
    return j


def _hyphen_break(text, i, n):
    """Whether the single hyphen at `text[i]` is CPython's word-internal
    hyphenation breakpoint -- preceded by two letters, or by
    letter-hyphen-letter-hyphen, and followed by letter[-]letter. If so, the
    index just past the hyphen (the break is AFTER it); otherwise None.
    """
    cond_a = i >= 2 and _is_letter(text[i - 2]) and _is_letter(text[i - 1])
    # letter-hyphen-letter-hyphen ending AT this hyphen (text[i] is already
    # known to be '-', so only the three characters before it are checked).
    cond_b = (i >= 3 and _is_letter(text[i - 3]) and text[i - 2] == '-'
              and _is_letter(text[i - 1]))
    if not (cond_a or cond_b):
        return None
    k = i + 1
    if k >= n or not _is_letter(text[k]):
        return None
    k += 1
    if k < n and text[k] == '-':
        k += 1
    if k >= n or not _is_letter(text[k]):
        return None
    return i + 1


def _wordsep_split(text):
    """`wordsep_re.split(text)`, filtered of the empty strings a real
    `re.split` would leave between adjacent matches -- see the module
    docstring for what this reproduces and why it is not a regex.
    """
    n = len(text)
    chunks = []
    i = 0
    while i < n:
        c = text[i]
        if _is_ws(c):
            j = i + 1
            while j < n and _is_ws(text[j]):
                j += 1
            chunks.append(text[i:j])
            i = j
            continue
        dash_end = _em_dash_run(text, i, n)
        if dash_end is not None:
            chunks.append(text[i:dash_end])
            i = dash_end
            continue
        start = i
        i += 1
        while True:
            if i >= n or _is_ws(text[i]):
                break
            if _em_dash_run(text, i, n) is not None:
                break
            if text[i] == '-':
                brk = _hyphen_break(text, i, n)
                if brk is not None:
                    i = brk
                    break
            i += 1
        chunks.append(text[start:i])
    return chunks


def _wordsep_simple_split(text):
    """`wordsep_simple_re.split(text)`, filtered the same way -- whitespace
    runs against everything else, with no hyphen handling at all.
    """
    n = len(text)
    chunks = []
    i = 0
    while i < n:
        c = text[i]
        j = i + 1
        if _is_ws(c):
            while j < n and _is_ws(text[j]):
                j += 1
        else:
            while j < n and not _is_ws(text[j]):
                j += 1
        chunks.append(text[i:j])
        i = j
    return chunks


def _replace_whitespace(text):
    """`text.translate(unicode_whitespace_trans)` -- every `_whitespace`
    character becomes a plain space, everything else is untouched.
    """
    out = []
    for c in text:
        if c in _whitespace:
            out.append(' ')
        else:
            out.append(c)
    return ''.join(out)


def _looks_like_sentence_end(chunk):
    """`sentence_end_re.search(chunk)` -- a lowercase ASCII letter, one of
    `. ! ?`, an optional closing quote, at the end of `chunk`.
    """
    s = chunk
    if s and (s[-1] == '"' or s[-1] == "'"):
        s = s[:-1]
    if len(s) < 2:
        return False
    c = s[-2]
    if not ('a' <= c <= 'z'):
        return False
    return s[-1] == '.' or s[-1] == '!' or s[-1] == '?'


class TextWrapper:
    """Object for wrapping/filling text -- CPython's own shape.  The public
    interface is `wrap()` and `fill()`; the rest exists for a subclass to
    override, same as CPython's.

      width (default: 70)
        the maximum width of wrapped lines (unless break_long_words
        is false)
      initial_indent (default: "")
        string that will be prepended to the first line of wrapped
        output.  Counts towards the line's width.
      subsequent_indent (default: "")
        string that will be prepended to all lines save the first
        of wrapped output; also counts towards each line's width.
      expand_tabs (default: true)
        Expand tabs in input text to spaces before further processing.
      tabsize (default: 8)
        Expand tabs in input text to 0 .. 'tabsize' spaces, unless
        'expand_tabs' is false.
      replace_whitespace (default: true)
        Replace all whitespace characters in the input text by spaces
        after tab expansion.
      fix_sentence_endings (default: false)
        Ensure that sentence-ending punctuation is always followed
        by two spaces.
      break_long_words (default: true)
        Break words longer than 'width'.  If false, those words will not
        be broken, and some lines might be longer than 'width'.
      break_on_hyphens (default: true)
        Allow breaking hyphenated words. If true, wrapping will occur
        preferably on whitespaces and right after hyphens part of
        compound words.
      drop_whitespace (default: true)
        Drop leading and trailing whitespace from lines.
      max_lines (default: None)
        Truncate wrapped lines.
      placeholder (default: ' [...]')
        Append to the last line of truncated text.
    """

    def __init__(self,
                 width=70,
                 initial_indent="",
                 subsequent_indent="",
                 expand_tabs=True,
                 replace_whitespace=True,
                 fix_sentence_endings=False,
                 break_long_words=True,
                 drop_whitespace=True,
                 break_on_hyphens=True,
                 tabsize=8,
                 *,
                 max_lines=None,
                 placeholder=' [...]'):
        self.width = width
        self.initial_indent = initial_indent
        self.subsequent_indent = subsequent_indent
        self.expand_tabs = expand_tabs
        self.replace_whitespace = replace_whitespace
        self.fix_sentence_endings = fix_sentence_endings
        self.break_long_words = break_long_words
        self.drop_whitespace = drop_whitespace
        self.break_on_hyphens = break_on_hyphens
        self.tabsize = tabsize
        self.max_lines = max_lines
        self.placeholder = placeholder

    # -- Private methods, mirroring CPython's shape -----------------------

    def _munge_whitespace(self, text):
        if self.expand_tabs:
            text = text.expandtabs(self.tabsize)
        if self.replace_whitespace:
            text = _replace_whitespace(text)
        return text

    def _split(self, text):
        if self.break_on_hyphens:
            chunks = _wordsep_split(text)
        else:
            chunks = _wordsep_simple_split(text)
        return chunks

    def _split_chunks(self, text):
        text = self._munge_whitespace(text)
        return self._split(text)

    def _fix_sentence_endings(self, chunks):
        i = 0
        while i < len(chunks) - 1:
            if chunks[i + 1] == " " and _looks_like_sentence_end(chunks[i]):
                chunks[i + 1] = "  "
                i += 2
            else:
                i += 1

    def _handle_long_word(self, reversed_chunks, cur_line, cur_len, width):
        if width < 1:
            space_left = 1
        else:
            space_left = width - cur_len

        if self.break_long_words:
            end = space_left
            chunk = reversed_chunks[-1]
            if self.break_on_hyphens and len(chunk) > space_left:
                hyphen = chunk.rfind('-', 0, space_left)
                if hyphen > 0 and _any_non_hyphen(chunk[:hyphen]):
                    end = hyphen + 1
            cur_line.append(chunk[:end])
            reversed_chunks[-1] = chunk[end:]
        elif not cur_line:
            cur_line.append(reversed_chunks.pop())

    def _wrap_chunks(self, chunks):
        lines = []
        if self.width <= 0:
            raise ValueError("invalid width %r (must be > 0)" % (self.width,))
        if self.max_lines is not None:
            if self.max_lines > 1:
                indent = self.subsequent_indent
            else:
                indent = self.initial_indent
            if len(indent) + len(self.placeholder.lstrip()) > self.width:
                raise ValueError("placeholder too large for max width")

        chunks.reverse()

        while chunks:
            cur_line = []
            cur_len = 0

            if lines:
                indent = self.subsequent_indent
            else:
                indent = self.initial_indent

            width = self.width - len(indent)

            if self.drop_whitespace and chunks[-1].strip() == '' and lines:
                del chunks[-1]

            while chunks:
                l = len(chunks[-1])
                if cur_len + l <= width:
                    cur_line.append(chunks.pop())
                    cur_len += l
                else:
                    break

            if chunks and len(chunks[-1]) > width:
                self._handle_long_word(chunks, cur_line, cur_len, width)
                cur_len = 0
                for piece in cur_line:
                    cur_len += len(piece)

            if self.drop_whitespace and cur_line and cur_line[-1].strip() == '':
                cur_len -= len(cur_line[-1])
                del cur_line[-1]

            if cur_line:
                inner = (not chunks) or (self.drop_whitespace
                                          and len(chunks) == 1
                                          and not chunks[0].strip())
                fits = (self.max_lines is None
                        or len(lines) + 1 < self.max_lines
                        or (inner and cur_len <= width))

                if fits:
                    lines.append(indent + ''.join(cur_line))
                else:
                    done = False
                    while cur_line:
                        if (cur_line[-1].strip() and
                                cur_len + len(self.placeholder) <= width):
                            cur_line.append(self.placeholder)
                            lines.append(indent + ''.join(cur_line))
                            done = True
                            break
                        cur_len -= len(cur_line[-1])
                        del cur_line[-1]
                    if not done:
                        if lines:
                            prev_line = lines[-1].rstrip()
                            if (len(prev_line) + len(self.placeholder)
                                    <= self.width):
                                lines[-1] = prev_line + self.placeholder
                                done = True
                        if not done:
                            lines.append(indent + self.placeholder.lstrip())
                    break

        return lines

    # -- Public interface ---------------------------------------------

    def wrap(self, text):
        chunks = self._split_chunks(text)
        if self.fix_sentence_endings:
            self._fix_sentence_endings(chunks)
        return self._wrap_chunks(chunks)

    def fill(self, text):
        return "\n".join(self.wrap(text))


# -- Convenience interface ---------------------------------------------

def wrap(text, width=70, **kwargs):
    w = TextWrapper(width=width, **kwargs)
    return w.wrap(text)


def fill(text, width=70, **kwargs):
    w = TextWrapper(width=width, **kwargs)
    return w.fill(text)


def shorten(text, width, **kwargs):
    w = TextWrapper(width=width, max_lines=1, **kwargs)
    return w.fill(' '.join(text.strip().split()))


# -- Loosely related functionality -------------------------------------

def dedent(text):
    """Remove any common leading whitespace from every line in `text`.

    Entirely blank lines are normalized to a newline character. Lines that
    are ENTIRELY whitespace do not count when computing the common margin --
    CPython's own rule, and the reason a blank line indented with the wrong
    kind of whitespace does not defeat dedenting the rest of the text.
    """
    lines = text.split('\n')
    non_blank_lines = [l for l in lines if l and not l.isspace()]
    if non_blank_lines:
        l1 = min(non_blank_lines)
        l2 = max(non_blank_lines)
    else:
        l1 = ''
        l2 = ''

    # A non_blank_line is never entirely whitespace (that is what excludes
    # it), so `l1` always holds a non-whitespace character within its own
    # length -- and `c not in ' \t'` breaks the loop there at the latest.
    # It always breaks (or `l1` is empty and the loop never runs), so
    # `margin` never needs a value from past the loop finishing on its own.
    margin = 0
    for idx in range(len(l1)):
        c = l1[idx]
        margin = idx
        if c != l2[idx] or c not in ' \t':
            break

    out_lines = []
    for l in lines:
        if l.isspace():
            out_lines.append('')
        else:
            out_lines.append(l[margin:])
    return '\n'.join(out_lines)


def indent(text, prefix, predicate=None):
    """Adds 'prefix' to the beginning of selected lines in 'text'.

    If 'predicate' is provided, 'prefix' will only be added to the lines
    where 'predicate(line)' is True. If 'predicate' is not provided,
    it will default to adding 'prefix' to all non-empty lines that do not
    consist solely of whitespace characters.
    """
    prefixed_lines = []
    for line in text.splitlines(True):
        if predicate is None:
            add = not line.isspace()
        else:
            add = predicate(line)
        if add:
            prefixed_lines.append(prefix)
        prefixed_lines.append(line)
    return ''.join(prefixed_lines)
