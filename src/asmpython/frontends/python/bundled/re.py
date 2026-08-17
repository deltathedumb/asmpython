"""`re`, as ordinary Python this compiler compiles.

COVERAGE: literals, `.`, `[...]` with ranges and negation, `* + ? {m} {m,}
{m,n}` and each one's non-greedy form, `|`, `(...)`, `(?:...)`,
`(?P<name>...)`, `(?P=name)`, `(?=...)`, `(?!...)`, `(?#...)`, inline flags
both global `(?i)` and scoped `(?i:...)`, `^ $`, the escapes `\\d \\D \\w \\W
\\s \\S \\b \\B \\A \\Z` and the character escapes `\\n \\t \\r \\f \\v \\a
\\0 \\xhh \\uhhhh \\UhhhhhhhH` and octal, and `\\1`..`\\99` backreferences.
The flags `I M S X A U L`. The surface `compile search match fullmatch findall
finditer sub subn split escape error purge Pattern Match`.

NOT COVERED: lookbehind `(?<=...)` and `(?<!...)`, conditional groups
`(?(1)...)`, atomic groups `(?>...)`, possessive quantifiers `*+`, `\\N{...}`,
unicode property escapes `\\p{...}`, `re.Scanner`, and `bytes` patterns. Each
is refused with a `re.error` naming it rather than silently doing something
else -- a regular-expression engine that quietly matches the wrong thing is
worse than one that says it cannot.

`A` (ASCII) is accepted and is the behaviour anyway: `\\w`, `\\d` and `\\s`
here are the ASCII definitions, so `re.ASCII` changes nothing and `re.UNICODE`
promises more than this delivers. Said here rather than discovered.

THE FLAGS ARE PLAIN INTEGERS and CPython's are an `enum.IntFlag`, so `re.I`
prints as `2` here and as `re.IGNORECASE` there. Every VALUE is CPython's, so
a pattern behaves the same and only the repr differs; it becomes an enum when
`enum` is rebuilt (`docs/STDLIB.md`).

## Why a tree and a matcher, and not `sre` bytecode

CPython compiles a pattern to bytecode for an interpreter written in C. That
form is faster and it is the wrong thing to copy here: it is an optimisation
of a machine this project does not have, and reading it back tells nobody what
the module means. A tree walked by a matcher that takes a CONTINUATION is the
form the semantics are stated in -- `a*` is "match `a` as many times as you
can, then ask the rest of the pattern, and give a character back each time it
says no" -- and it is the form a divergence can be read out of.

## The continuation

Every node's `match(ctx, pos, cont)` answers the position the WHOLE pattern
ended at, or -1. It does not answer where the node itself ended, because that
is not enough to backtrack on: a node that can match in several ways has to
know whether what FOLLOWS it succeeded, and `cont` is how it asks. That single
decision is what makes greedy and non-greedy repetition four lines apart
rather than two different algorithms.
"""
NOFLAG = 0
ASCII = 256
A = 256
DEBUG = 128
IGNORECASE = 2
I = 2
LOCALE = 4
L = 4
MULTILINE = 8
M = 8
DOTALL = 16
S = 16
UNICODE = 32
U = 32
VERBOSE = 64
X = 64

_FLAG_LETTERS = {"a": 256, "i": 2, "L": 4, "m": 8, "s": 16, "u": 32, "x": 64}

_DIGITS = "0123456789"
_HEX = "0123456789abcdefABCDEF"
_WORD = ("abcdefghijklmnopqrstuvwxyz"
         "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SPACE = " \t\n\r\f\v"


class error(Exception):
    """A pattern this module cannot compile, and where it gave up.

    `pos` is the offset into the pattern rather than into the subject: a bad
    pattern is a bug in the program, and the program is what the position has
    to point into for anyone to fix it.
    """

    def __init__(self, msg, pattern=None, pos=None):
        self.msg = msg
        self.pattern = pattern
        self.pos = pos
        self.lineno = None
        self.colno = None
        text = msg
        if pattern is not None and pos is not None:
            text = "%s at position %d" % (msg, pos)
            self.lineno = pattern.count("\n", 0, pos) + 1
            self.colno = pos - pattern.rfind("\n", 0, pos)
        super().__init__(text)


# ── the character predicates ────────────────────────────────────────────────
# ASCII, and the module docstring says so. A `\\w` that claimed to know about
# every unicode letter and did not would be a wrong answer wearing a right
# name, and this runtime has no character database to consult.

def _is_word(ch):
    return ch in _WORD


def _is_digit(ch):
    return ch in _DIGITS


def _is_space(ch):
    return ch in _SPACE


def _cat_holds(cat, ch):
    if cat == "d":
        return _is_digit(ch)
    if cat == "D":
        return not _is_digit(ch)
    if cat == "w":
        return _is_word(ch)
    if cat == "W":
        return not _is_word(ch)
    if cat == "s":
        return _is_space(ch)
    return not _is_space(ch)


class _Item:
    """One entry of a character class: a character, a range, or a category.

    ONE CLASS RATHER THAN THREE SHAPES OF TUPLE. A list holding `("ch", c)`
    beside `("range", a, b)` is a list whose elements have different lengths,
    and every reader of it has to know which is which from `item[0]`.
    """

    def __init__(self, kind, a, b):
        self.kind = kind
        self.a = a
        self.b = b

    def holds(self, ch, fold):
        if self.kind == "cat":
            return _cat_holds(self.a, ch)
        if self.kind == "ch":
            if self.a == ch:
                return True
            return fold and self.a.lower() == ch.lower()
        if self.a <= ch and ch <= self.b:
            return True
        if not fold:
            return False
        lo = ch.lower()
        up = ch.upper()
        return (self.a <= lo and lo <= self.b) or (self.a <= up and up <= self.b)


# ── the nodes ───────────────────────────────────────────────────────────────

def _keep(pos):
    """The continuation that accepts wherever it is handed.

    Used where a node's OWN extent is the question -- a lookahead, or the
    one-character probe a simple repetition scans with -- rather than the
    whole pattern's.
    """
    return pos


class _Lit:
    def __init__(self, ch):
        self.ch = ch

    def match(self, ctx, pos, cont):
        if pos >= ctx.end:
            return -1
        got = ctx.text[pos]
        if got != self.ch:
            if not (ctx.flags & IGNORECASE) or got.lower() != self.ch.lower():
                return -1
        return cont(pos + 1)


class _Any:
    def match(self, ctx, pos, cont):
        if pos >= ctx.end:
            return -1
        if not (ctx.flags & DOTALL) and ctx.text[pos] == "\n":
            return -1
        return cont(pos + 1)


class _Klass:
    def __init__(self, items, negate):
        self.items = items
        self.negate = negate

    def match(self, ctx, pos, cont):
        if pos >= ctx.end:
            return -1
        ch = ctx.text[pos]
        fold = (ctx.flags & IGNORECASE) != 0
        hit = False
        for item in self.items:
            if item.holds(ch, fold):
                hit = True
                break
        if hit == self.negate:
            return -1
        return cont(pos + 1)


class _Seq:
    """Several nodes in a row, threaded so each one's continuation is the
    rest of the sequence followed by whatever the sequence itself was given."""

    def __init__(self, nodes):
        self.nodes = nodes

    def match(self, ctx, pos, cont):
        return _seq_from(self.nodes, 0, ctx, pos, cont)


def _seq_from(nodes, i, ctx, pos, cont):
    if i >= len(nodes):
        return cont(pos)

    def rest(at):
        return _seq_from(nodes, i + 1, ctx, at, cont)

    return nodes[i].match(ctx, pos, rest)


class _Alt:
    """`a|b` -- the branches in order, and the FIRST that lets the rest of the
    pattern through wins. Not the longest: Python's is a backtracking engine
    and leftmost-first is the rule programs are written against."""

    def __init__(self, branches):
        self.branches = branches

    def match(self, ctx, pos, cont):
        for branch in self.branches:
            saved = ctx.mark[:]
            got = branch.match(ctx, pos, cont)
            if got >= 0:
                return got
            ctx.mark = saved
        return -1


class _Group:
    """`(...)` -- the same as its body, and it records where it ran.

    THE SPAN IS WRITTEN WHEN THE BODY FINISHES AND UNWOUND IF THE REST FAILS.
    A group that matched inside an attempt that was later abandoned must not
    be readable afterwards: `(a)|b` against "b" leaves group 1 unset, and
    writing it at the moment the body succeeded would leave "a" behind.
    """

    def __init__(self, index, node):
        self.index = index
        self.node = node

    def match(self, ctx, pos, cont):
        index = self.index
        node = self.node

        def close(at):
            was_s = ctx.mark[2 * index]
            was_e = ctx.mark[2 * index + 1]
            was_last = ctx.last
            ctx.mark[2 * index] = pos
            ctx.mark[2 * index + 1] = at
            # `lastindex` IS THE GROUP THAT CLOSED LAST, which is why it is
            # recorded here and not by counting. For `((a))` the inner group
            # closes first and the outer second, so CPython answers 1 -- and
            # anything derived from the group numbers alone answers 2.
            ctx.last = index
            got = cont(at)
            if got < 0:
                ctx.mark[2 * index] = was_s
                ctx.mark[2 * index + 1] = was_e
                ctx.last = was_last
            return got

        return node.match(ctx, pos, close)


class _Flags:
    """`(?i:...)` -- the flags in force for one subexpression.

    Swapped on the way in and restored on the way out INCLUDING through the
    continuation: what follows the group is outside it, so `(?i:a)b` folds the
    `a` and not the `b`.
    """

    def __init__(self, node, add, drop):
        self.node = node
        self.add = add
        self.drop = drop

    def match(self, ctx, pos, cont):
        outer = ctx.flags
        inner = (outer | self.add) & ~self.drop

        def leave(at):
            ctx.flags = outer
            got = cont(at)
            if got < 0:
                ctx.flags = inner
            return got

        ctx.flags = inner
        got = self.node.match(ctx, pos, leave)
        ctx.flags = outer
        return got


class _Backref:
    """`\\1` -- whatever that group matched, again, literally."""

    def __init__(self, index):
        self.index = index

    def match(self, ctx, pos, cont):
        start = ctx.mark[2 * self.index]
        stop = ctx.mark[2 * self.index + 1]
        # A GROUP THAT NEVER MATCHED FAILS THE REFERENCE. It does not match
        # empty: `(a)?\\1` against "" is a failure in CPython, and treating an
        # unset group as "" would make it succeed.
        if start < 0 or stop < 0:
            return -1
        want = ctx.text[start:stop]
        n = len(want)
        if pos + n > ctx.end:
            return -1
        have = ctx.text[pos:pos + n]
        if have != want:
            if not (ctx.flags & IGNORECASE) or have.lower() != want.lower():
                return -1
        return cont(pos + n)


class _Assert:
    """A position rather than a character: `^ $ \\b \\B \\A \\Z`."""

    def __init__(self, kind):
        self.kind = kind

    def match(self, ctx, pos, cont):
        if not self.holds(ctx, pos):
            return -1
        return cont(pos)

    def holds(self, ctx, pos):
        kind = self.kind
        text = ctx.text
        if kind == "A":
            return pos == 0
        if kind == "Z":
            return pos == ctx.end
        if kind == "^":
            if pos == 0:
                return True
            return (ctx.flags & MULTILINE) != 0 and text[pos - 1] == "\n"
        if kind == "$":
            if pos == ctx.end:
                return True
            if (ctx.flags & MULTILINE) != 0 and text[pos] == "\n":
                return True
            # `$` ALSO MATCHES BEFORE A TRAILING NEWLINE, without MULTILINE.
            # That is not a special case of the line rule: it is the one
            # place a non-multiline `$` looks at a character at all.
            return pos == ctx.end - 1 and text[pos] == "\n"
        before = pos > 0 and _is_word(text[pos - 1])
        after = pos < ctx.end and _is_word(text[pos])
        if kind == "b":
            return before != after
        return before == after


class _Look:
    """`(?=...)` and `(?!...)` -- match here and consume nothing."""

    def __init__(self, node, negate):
        self.node = node
        self.negate = negate

    def match(self, ctx, pos, cont):
        saved = ctx.mark[:]
        was_last = ctx.last
        hit = self.node.match(ctx, pos, _keep) >= 0
        if self.negate:
            # A NEGATIVE LOOKAHEAD NEVER KEEPS GROUPS, whether or not it
            # matched: it succeeds when its body failed, and a body that
            # failed set nothing worth keeping.
            ctx.mark = saved
            ctx.last = was_last
            if hit:
                return -1
            return cont(pos)
        if not hit:
            ctx.mark = saved
            ctx.last = was_last
            return -1
        # A POSITIVE ONE DOES KEEP THEM. `(?=(a))` leaves group 1 set to "a",
        # which is how a program reads a piece of text without consuming it.
        got = cont(pos)
        if got < 0:
            ctx.mark = saved
            ctx.last = was_last
        return got


class _Repeat:
    """`a*`, `a+`, `a?`, `a{m,n}` and each one's non-greedy form.

    TWO IMPLEMENTATIONS OF ONE MEANING, and the fast one is not an
    optimisation for its own sake. A repetition of something one character
    wide that captures nothing -- `.*`, `\\d+`, `[a-z]{2,4}`, which is most of
    them -- can be scanned in a loop and backtracked by arithmetic. The
    general one recurses once per repetition, so `.*` over a long subject
    would be as deep as the subject is long, and this runtime's stack is not.
    """

    def __init__(self, node, lo, hi, greedy):
        self.node = node
        self.lo = lo
        self.hi = hi
        self.greedy = greedy
        self.simple = isinstance(node, (_Lit, _Any, _Klass))

    def match(self, ctx, pos, cont):
        if self.simple:
            return self.scan(ctx, pos, cont)
        return self.walk(ctx, pos, 0, cont)

    def scan(self, ctx, pos, cont):
        node = self.node
        hi = self.hi
        at = pos
        count = 0
        while hi < 0 or count < hi:
            if node.match(ctx, at, _keep) < 0:
                break
            at = at + 1
            count = count + 1
        if count < self.lo:
            return -1
        if self.greedy:
            while count >= self.lo:
                got = cont(pos + count)
                if got >= 0:
                    return got
                count = count - 1
            return -1
        take = self.lo
        while take <= count:
            got = cont(pos + take)
            if got >= 0:
                return got
            take = take + 1
        return -1

    def walk(self, ctx, pos, done, cont):
        node = self.node
        lo = self.lo
        hi = self.hi
        greedy = self.greedy

        def again(at):
            # AN EMPTY REPETITION STOPS. `(a?)*` can match nothing forever,
            # and CPython's engine has the same guard: once the minimum is
            # met, a body that consumed nothing is not tried again.
            if at == pos and done + 1 > lo:
                return -1
            return self.walk(ctx, at, done + 1, cont)

        if greedy:
            if hi < 0 or done < hi:
                got = node.match(ctx, pos, again)
                if got >= 0:
                    return got
            if done >= lo:
                return cont(pos)
            return -1
        if done >= lo:
            got = cont(pos)
            if got >= 0:
                return got
        if hi < 0 or done < hi:
            return node.match(ctx, pos, again)
        return -1


class _Ctx:
    """What the matcher carries: the subject, the flags in force, and the
    group slots. `mark[2i]` and `mark[2i+1]` are group `i`'s span."""

    def __init__(self, text, flags, ngroups, end):
        self.text = text
        self.flags = flags
        self.end = end
        self.last = -1
        self.mark = [-1] * (2 * (ngroups + 1))


# ── the parser ──────────────────────────────────────────────────────────────

class _Parser:
    def __init__(self, pattern, flags):
        self.p = pattern
        self.i = 0
        self.n = len(pattern)
        self.flags = flags
        self.ngroups = 0
        self.names = {}

    def fail(self, msg, at=None):
        raise error(msg, self.p, self.i if at is None else at)

    def parse(self):
        node = self.alternation()
        if self.i < self.n:
            if self.p[self.i] == ")":
                self.fail("unbalanced parenthesis")
            self.fail("unexpected character")
        return node

    def alternation(self):
        branches = [self.sequence()]
        while self.i < self.n and self.p[self.i] == "|":
            self.i = self.i + 1
            branches.append(self.sequence())
        if len(branches) == 1:
            return branches[0]
        return _Alt(branches)

    def sequence(self):
        nodes = []
        while self.i < self.n:
            ch = self.p[self.i]
            if ch == "|" or ch == ")":
                break
            node = self.atom()
            # `None` IS A PIECE OF SYNTAX THAT MATCHES NOTHING -- a comment, or
            # whitespace under VERBOSE. It is not an error and it is not a
            # node; a quantifier after one would have nothing to repeat, which
            # is what CPython says too.
            if node is None:
                continue
            nodes.append(self.quantified(node))
        return _Seq(nodes)

    def atom(self):
        ch = self.p[self.i]
        if self.flags & VERBOSE:
            if ch in _SPACE:
                self.i = self.i + 1
                return None
            if ch == "#":
                while self.i < self.n and self.p[self.i] != "\n":
                    self.i = self.i + 1
                return None
        if ch == "(":
            return self.group()
        if ch == "[":
            return self.klass()
        if ch == ".":
            self.i = self.i + 1
            return _Any()
        if ch == "^":
            self.i = self.i + 1
            return _Assert("^")
        if ch == "$":
            self.i = self.i + 1
            return _Assert("$")
        if ch == "\\":
            return self.escape()
        if ch == "*" or ch == "+" or ch == "?":
            self.fail("nothing to repeat")
        self.i = self.i + 1
        return _Lit(ch)

    def group(self):
        start = self.i
        self.i = self.i + 1
        if self.i < self.n and self.p[self.i] == "?":
            made = self.extension(start)
            if made is not _MORE:
                return made
            # An extension that only changed the flags leaves an ordinary
            # group behind; fall through to the capture below.
        return self.capturing(None)

    def extension(self, start):
        """Everything spelled `(?...)`. Answers `_MORE` when what it read was
        a scoped-flag prefix and the body still has to be parsed."""
        self.i = self.i + 1
        if self.i >= self.n:
            self.fail("unexpected end of pattern")
        ch = self.p[self.i]
        if ch == ":":
            self.i = self.i + 1
            node = self.alternation()
            self.expect(")")
            return node
        if ch == "=" or ch == "!":
            self.i = self.i + 1
            node = self.alternation()
            self.expect(")")
            return _Look(node, ch == "!")
        if ch == "#":
            while self.i < self.n and self.p[self.i] != ")":
                self.i = self.i + 1
            self.expect(")")
            return None
        if ch == "<":
            if self.i + 1 < self.n and self.p[self.i + 1] in "=!":
                self.fail("lookbehind is not supported by this "
                          "implementation", start)
            self.fail("unknown extension ?<", start)
        if ch == ">":
            self.fail("atomic groups are not supported by this "
                      "implementation", start)
        if ch == "(":
            self.fail("conditional groups are not supported by this "
                      "implementation", start)
        if ch == "P":
            self.i = self.i + 1
            if self.i >= self.n:
                self.fail("unexpected end of pattern")
            if self.p[self.i] == "<":
                self.i = self.i + 1
                return self.capturing(self.name(">"))
            if self.p[self.i] == "=":
                self.i = self.i + 1
                want = self.name(")")
                if want not in self.names:
                    self.fail("unknown group name %r" % (want,))
                return _Backref(self.names[want])
            self.fail("unknown extension ?P", start)
        return self.inline(start)

    def inline(self, start):
        """`(?i)`, `(?i-s)` and `(?i:...)`."""
        add = 0
        drop = 0
        into = "add"
        seen = False
        while self.i < self.n:
            ch = self.p[self.i]
            if ch == "-":
                if into == "drop":
                    self.fail("bad inline flags", start)
                into = "drop"
                self.i = self.i + 1
                continue
            if ch not in _FLAG_LETTERS:
                break
            bit = _FLAG_LETTERS[ch]
            if into == "add":
                add = add | bit
            else:
                drop = drop | bit
            seen = True
            self.i = self.i + 1
        if not seen:
            self.fail("unknown extension ?" + self.p[self.i:self.i + 1], start)
        if self.i < self.n and self.p[self.i] == ":":
            self.i = self.i + 1
            keep = self.flags
            # PARSED UNDER THE INNER FLAGS TOO, not only matched under them:
            # VERBOSE decides which characters are syntax, so `(?x: a b )` has
            # to be READ with whitespace ignored or the node tree is wrong
            # before any subject exists.
            self.flags = (keep | add) & ~drop
            node = self.alternation()
            self.flags = keep
            self.expect(")")
            return _Flags(node, add, drop)
        self.expect(")")
        # A GLOBAL SETTING, and it applies to the whole pattern including what
        # came before it. CPython requires these at the start for exactly that
        # reason; accepting them anywhere and applying them everywhere is the
        # same rule stated without the position check.
        self.flags = (self.flags | add) & ~drop
        return None

    def capturing(self, name):
        self.ngroups = self.ngroups + 1
        index = self.ngroups
        if name is not None:
            if name in self.names:
                self.fail("redefinition of group name %r" % (name,))
            self.names[name] = index
        node = self.alternation()
        self.expect(")")
        return _Group(index, node)

    def name(self, closer):
        out = []
        while self.i < self.n and self.p[self.i] != closer:
            out.append(self.p[self.i])
            self.i = self.i + 1
        if self.i >= self.n:
            self.fail("missing %s" % (closer,))
        self.i = self.i + 1
        text = "".join(out)
        if not text:
            self.fail("missing group name")
        for ch in text:
            if ch not in _WORD:
                self.fail("bad character in group name %r" % (text,))
        return text

    def expect(self, ch):
        if self.i >= self.n or self.p[self.i] != ch:
            if ch == ")":
                self.fail("missing ), unterminated subpattern")
            self.fail("expected %r" % (ch,))
        self.i = self.i + 1

    def digits(self):
        out = []
        while self.i < self.n and self.p[self.i] in _DIGITS:
            out.append(self.p[self.i])
            self.i = self.i + 1
        return "".join(out)

    def quantified(self, node):
        if self.i >= self.n:
            return node
        ch = self.p[self.i]
        lo = 0
        hi = -1
        if ch == "*":
            self.i = self.i + 1
        elif ch == "+":
            self.i = self.i + 1
            lo = 1
        elif ch == "?":
            self.i = self.i + 1
            hi = 1
        elif ch == "{":
            was = self.i
            self.i = self.i + 1
            first = self.digits()
            if self.i < self.n and self.p[self.i] == ",":
                self.i = self.i + 1
                second = self.digits()
            else:
                second = first
            if (self.i >= self.n or self.p[self.i] != "}"
                    or (first == "" and second == "")):
                # NOT A QUANTIFIER AFTER ALL, so `{` is an ordinary character
                # -- `a{b}` and `{}` are literal in Python, and rejecting them
                # would break patterns that are already correct.
                self.i = was
                return node
            self.i = self.i + 1
            lo = int(first) if first != "" else 0
            hi = int(second) if second != "" else -1
            if hi >= 0 and hi < lo:
                self.fail("min repeat greater than max repeat")
        else:
            return node
        greedy = True
        if self.i < self.n and self.p[self.i] == "?":
            self.i = self.i + 1
            greedy = False
        elif self.i < self.n and self.p[self.i] == "+":
            self.fail("possessive quantifiers are not supported by this "
                      "implementation")
        if isinstance(node, _Assert):
            self.fail("nothing to repeat")
        return _Repeat(node, lo, hi, greedy)

    def escape(self):
        self.i = self.i + 1
        if self.i >= self.n:
            self.fail("bad escape (end of pattern)")
        ch = self.p[self.i]
        self.i = self.i + 1
        if ch in "dDwWsS":
            return _Klass([_Item("cat", ch, "")], False)
        if ch in "bBAZ":
            return _Assert(ch)
        if ch == "N":
            self.fail("\\N{...} is not supported by this implementation")
        if ch == "p" or ch == "P":
            self.fail("unicode property escapes are not supported by this "
                      "implementation")
        if ch in "123456789":
            digits = ch
            while (self.i < self.n and self.p[self.i] in _DIGITS
                   and int(digits + self.p[self.i]) <= self.ngroups):
                digits = digits + self.p[self.i]
                self.i = self.i + 1
            index = int(digits)
            if index > self.ngroups:
                self.fail("invalid group reference %d" % (index,))
            return _Backref(index)
        return _Lit(self.charescape(ch))

    def charescape(self, ch):
        """The escapes that stand for one CHARACTER, shared with `[...]`."""
        simple = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v",
                  "a": "\a", "b": "\b", "0": "\0"}
        if ch in simple:
            return simple[ch]
        if ch == "x":
            return chr(self.hexdigits(2, "incomplete escape \\x"))
        if ch == "u":
            return chr(self.hexdigits(4, "incomplete escape \\u"))
        if ch == "U":
            value = self.hexdigits(8, "incomplete escape \\U")
            if value > 0x10FFFF:
                self.fail("bad escape \\U")
            return chr(value)
        if ch in _DIGITS:
            # OCTAL, and only where a backreference could not be meant. The
            # caller has already taken `\\1`..`\\99`; what reaches here starts
            # with `\\0` or is inside a character class, where no group can be.
            digits = ch
            while (len(digits) < 3 and self.i < self.n
                   and self.p[self.i] in "01234567"):
                digits = digits + self.p[self.i]
                self.i = self.i + 1
            value = 0
            for one in digits:
                value = value * 8 + (ord(one) - 48)
            return chr(value)
        if ch in _WORD and ch not in "_":
            # A LETTER OR DIGIT WITH NO MEANING IS AN ERROR, not a literal.
            # CPython made it one deliberately: `\\d` means something and
            # `\\q` almost certainly means the author thought it did too.
            self.fail("bad escape \\%s" % (ch,))
        return ch

    def hexdigits(self, count, msg):
        got = self.p[self.i:self.i + count]
        if len(got) < count:
            self.fail(msg)
        value = 0
        for ch in got:
            if ch not in _HEX:
                self.fail(msg)
            digit = ord(ch)
            if digit >= 97:
                digit = digit - 87
            elif digit >= 65:
                digit = digit - 55
            else:
                digit = digit - 48
            value = value * 16 + digit
        self.i = self.i + count
        return value

    def klass(self):
        self.i = self.i + 1
        negate = False
        if self.i < self.n and self.p[self.i] == "^":
            negate = True
            self.i = self.i + 1
        items = []
        first = True
        while True:
            if self.i >= self.n:
                self.fail("unterminated character set")
            ch = self.p[self.i]
            if ch == "]" and not first:
                self.i = self.i + 1
                break
            first = False
            if ch == "\\":
                self.i = self.i + 1
                if self.i >= self.n:
                    self.fail("bad escape (end of pattern)")
                esc = self.p[self.i]
                self.i = self.i + 1
                if esc in "dDwWsS":
                    items.append(_Item("cat", esc, ""))
                    continue
                low = self.charescape(esc)
            else:
                self.i = self.i + 1
                low = ch
            if (self.i + 1 < self.n and self.p[self.i] == "-"
                    and self.p[self.i + 1] != "]"):
                self.i = self.i + 1
                high = self.p[self.i]
                if high == "\\":
                    self.i = self.i + 1
                    if self.i >= self.n:
                        self.fail("bad escape (end of pattern)")
                    high = self.charescape(self.p[self.i])
                    self.i = self.i + 1
                else:
                    self.i = self.i + 1
                if ord(high) < ord(low):
                    self.fail("bad character range %s-%s" % (low, high))
                items.append(_Item("range", low, high))
                continue
            items.append(_Item("ch", low, ""))
        return _Klass(items, negate)


# ── the match ───────────────────────────────────────────────────────────────

class Match:
    """What a successful match answers. The spans, and the ways to read them.

    THE SPANS AND NOT THE TEXT. A group is a pair of offsets into the subject
    until someone asks for it, which is what makes `m.start(1)` exact and
    `m.group(1)` a slice taken on demand rather than a copy nobody wanted.
    """

    def __init__(self, pattern, string, pos, endpos, mark, last):
        self.re = pattern
        self.string = string
        self.pos = pos
        self.endpos = endpos
        self.regs = mark
        self.lastindex = None if last < 0 else last
        self.lastgroup = None
        if last >= 0:
            for name in pattern.groupindex:
                if pattern.groupindex[name] == last:
                    self.lastgroup = name
                    break

    def _index(self, which):
        if isinstance(which, str):
            if which not in self.re.groupindex:
                raise IndexError("no such group")
            return self.re.groupindex[which]
        if which < 0 or which > self.re.groups:
            raise IndexError("no such group")
        return which

    def _text(self, which):
        index = self._index(which)
        start = self.regs[2 * index]
        stop = self.regs[2 * index + 1]
        if start < 0 or stop < 0:
            return None
        return self.string[start:stop]

    def group(self, *which):
        if not which:
            return self._text(0)
        if len(which) == 1:
            return self._text(which[0])
        out = []
        for one in which:
            out.append(self._text(one))
        return tuple(out)

    def __getitem__(self, which):
        return self._text(which)

    def groups(self, default=None):
        out = []
        for index in range(1, self.re.groups + 1):
            got = self._text(index)
            out.append(default if got is None else got)
        return tuple(out)

    def groupdict(self, default=None):
        out = {}
        for name in self.re.groupindex:
            got = self._text(self.re.groupindex[name])
            out[name] = default if got is None else got
        return out

    def start(self, which=0):
        return self.regs[2 * self._index(which)]

    def end(self, which=0):
        return self.regs[2 * self._index(which) + 1]

    def span(self, which=0):
        index = self._index(which)
        return (self.regs[2 * index], self.regs[2 * index + 1])

    def expand(self, template):
        return _expand(self, template)

    def __repr__(self):
        return "<re.Match object; span=%r, match=%r>" % (self.span(),
                                                         self.group(0))


def _expand(match, template):
    """`\\1`, `\\g<1>`, `\\g<name>` and the character escapes, in a
    replacement.

    A GROUP THAT DID NOT MATCH EXPANDS TO EMPTY, where the same group read
    through `m.group(1)` answers None. That asymmetry is CPython's and it is
    the useful one: a template is text being built, and `None` in the middle
    of it is not text.
    """
    out = []
    i = 0
    n = len(template)
    while i < n:
        ch = template[i]
        if ch != "\\":
            out.append(ch)
            i = i + 1
            continue
        i = i + 1
        if i >= n:
            raise error("bad escape (end of pattern)")
        ch = template[i]
        i = i + 1
        if ch == "g":
            if i >= n or template[i] != "<":
                raise error("missing <")
            i = i + 1
            name = []
            while i < n and template[i] != ">":
                name.append(template[i])
                i = i + 1
            if i >= n:
                raise error("missing >, unterminated name")
            i = i + 1
            want = "".join(name)
            if not want:
                raise error("missing group name")
            if want[0] in _DIGITS:
                got = match.group(int(want))
            else:
                got = match.group(want)
            out.append("" if got is None else got)
            continue
        if ch in _DIGITS:
            digits = ch
            while (len(digits) < 2 and i < n and template[i] in _DIGITS):
                digits = digits + template[i]
                i = i + 1
            got = match.group(int(digits))
            out.append("" if got is None else got)
            continue
        simple = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v",
                  "a": "\a", "b": "\b", "\\": "\\"}
        if ch in simple:
            out.append(simple[ch])
            continue
        if ch in _WORD:
            raise error("bad escape \\%s" % (ch,))
        out.append("\\")
        out.append(ch)
    return "".join(out)


# ── the pattern ─────────────────────────────────────────────────────────────

class Pattern:
    def __init__(self, pattern, flags, root, ngroups, names):
        self.pattern = pattern
        self.flags = flags
        self.groups = ngroups
        self.groupindex = names
        self._root = root

    def __repr__(self):
        return "re.compile(%r)" % (self.pattern,)

    def _run(self, string, pos, endpos, anchored, whole):
        end = len(string) if endpos is None else min(endpos, len(string))
        if pos < 0:
            pos = 0
        if pos > end:
            return None
        if whole:
            def cont(at):
                return at if at == end else -1
        else:
            cont = _keep
        at = pos
        while True:
            ctx = _Ctx(string, self.flags, self.groups, end)
            got = self._root.match(ctx, at, cont)
            if got >= 0:
                ctx.mark[0] = at
                ctx.mark[1] = got
                return Match(self, string, pos, end, ctx.mark, ctx.last)
            if anchored or at >= end:
                return None
            at = at + 1

    def search(self, string, pos=0, endpos=None):
        return self._run(string, pos, endpos, False, False)

    def match(self, string, pos=0, endpos=None):
        return self._run(string, pos, endpos, True, False)

    def fullmatch(self, string, pos=0, endpos=None):
        return self._run(string, pos, endpos, True, True)

    def finditer(self, string, pos=0, endpos=None):
        end = len(string) if endpos is None else min(endpos, len(string))
        at = pos
        while at <= end:
            got = self._run(string, at, end, False, False)
            if got is None:
                break
            yield got
            # AN EMPTY MATCH ADVANCES BY ONE. Without that the same position
            # answers forever; with it, `findall(r'x*', 'axbxc')` gives the
            # six matches CPython gives rather than one and a hang.
            if got.end() == got.start():
                at = got.end() + 1
            else:
                at = got.end()

    def findall(self, string, pos=0, endpos=None):
        out = []
        for got in self.finditer(string, pos, endpos):
            if self.groups == 0:
                out.append(got.group(0))
            elif self.groups == 1:
                one = got.group(1)
                out.append("" if one is None else one)
            else:
                out.append(got.groups(""))
        return out

    def subn(self, repl, string, count=0):
        out = []
        made = 0
        last = 0
        for got in self.finditer(string):
            if count and made >= count:
                break
            out.append(string[last:got.start()])
            if callable(repl):
                out.append(repl(got))
            else:
                out.append(_expand(got, repl))
            last = got.end()
            made = made + 1
        out.append(string[last:])
        return ("".join(out), made)

    def sub(self, repl, string, count=0):
        return self.subn(repl, string, count)[0]

    def split(self, string, maxsplit=0):
        out = []
        made = 0
        last = 0
        for got in self.finditer(string):
            if maxsplit and made >= maxsplit:
                break
            out.append(string[last:got.start()])
            for index in range(1, self.groups + 1):
                out.append(got.group(index))
            last = got.end()
            made = made + 1
        out.append(string[last:])
        return out


# ── the module surface ──────────────────────────────────────────────────────

_cache = {}
_MORE = _Item("more", "", "")


def _build(pattern, flags):
    parser = _Parser(pattern, flags)
    root = parser.parse()
    # THE PARSER'S FLAGS AND NOT THE CALLER'S. A global `(?i)` anywhere in the
    # pattern sets one, and the compiled object has to carry what it will
    # actually match under or `p.flags` lies about it.
    return Pattern(pattern, parser.flags, root, parser.ngroups, parser.names)


def compile(pattern, flags=0):
    if isinstance(pattern, Pattern):
        if flags:
            raise ValueError("cannot process flags argument with a compiled "
                             "pattern")
        return pattern
    key = (pattern, flags)
    got = _cache.get(key)
    if got is not None:
        return got
    made = _build(pattern, flags)
    # BOUNDED, and cleared rather than evicted one at a time. A program that
    # builds patterns from data would otherwise grow this forever, and which
    # entry to drop is a question nobody here can answer better than starting
    # again.
    if len(_cache) >= 512:
        _cache.clear()
    _cache[key] = made
    return made


def purge():
    _cache.clear()


def search(pattern, string, flags=0):
    return compile(pattern, flags).search(string)


def match(pattern, string, flags=0):
    return compile(pattern, flags).match(string)


def fullmatch(pattern, string, flags=0):
    return compile(pattern, flags).fullmatch(string)


def findall(pattern, string, flags=0):
    return compile(pattern, flags).findall(string)


def finditer(pattern, string, flags=0):
    return compile(pattern, flags).finditer(string)


def sub(pattern, repl, string, count=0, flags=0):
    return compile(pattern, flags).sub(repl, string, count)


def subn(pattern, repl, string, count=0, flags=0):
    return compile(pattern, flags).subn(repl, string, count)


def split(pattern, string, maxsplit=0, flags=0):
    return compile(pattern, flags).split(string, maxsplit)


#: EXACTLY CPYTHON'S SET. `re.escape` stopped escaping every non-alphanumeric
#: in 3.7, and a program that builds a pattern by concatenation depends on the
#: result being the same string CPython would have produced.
_SPECIAL = "()[]{}?*+-|^$\\.&~# \t\n\r\v\f"


def escape(pattern):
    out = []
    for ch in pattern:
        if ch in _SPECIAL:
            out.append("\\")
        out.append(ch)
    return "".join(out)
