# COVERAGE: literals, `.`, character classes with ranges and negation, every
# quantifier greedy and non-greedy, alternation, capturing and non-capturing
# and named groups, backreferences, lookahead, anchors and word boundaries,
# the escapes, inline flags both global and scoped, the flags I M S X, and the
# surface compile/search/match/fullmatch/findall/finditer/sub/subn/split/
# escape with Match and Pattern. NOT covered here: lookbehind, conditional
# groups, atomic groups, possessive quantifiers, \N{...}, unicode property
# escapes, bytes patterns -- the module declares it refuses each of those, and
# the refusals ARE tested at the end.
#
# The subject strings are chosen so that a WRONG answer is a different string
# rather than a missing one: greedy and non-greedy over the same pattern, an
# empty match in the middle of a subject, a group that participates in one
# branch and not the other.
import re

# ---- the shapes ------------------------------------------------------------
print(re.match(r"a+b", "aaab").group(0))
print(re.match(r"a+?b", "aaab").group(0))
print(re.search(r"o", "foo").span())
print(re.search(r"x", "foo"))
print(re.fullmatch(r"[abc]{2,3}", "abc").group(0))
print(re.fullmatch(r"[abc]{2,3}", "abcd"))
print(re.match(r"a{2}", "aaa").group(0))
print(re.match(r"a{2,}", "aaaa").group(0))
print(re.match(r"a{,2}", "aaaa").group(0))
print(re.match(r"a{2,3}?", "aaaa").group(0))

# GREEDY AND NON-GREEDY OVER THE SAME SUBJECT is the pair that catches a
# quantifier implemented as "match as much as possible and never give back".
print(re.match(r"<.*>", "<a><b>").group(0))
print(re.match(r"<.*?>", "<a><b>").group(0))
print(re.findall(r"\w+?", "abc"))

# `{` THAT IS NOT A QUANTIFIER IS A LITERAL, which is why `a{b}` compiles.
print(re.match(r"a{b}", "a{b}").group(0))
print(re.findall(r"x{}", "x{}"))

# ---- character classes -----------------------------------------------------
print(re.findall(r"[a-cx-z]", "abcdxyz"))
print(re.findall(r"[^a-z]", "a1b2C"))
print(re.findall(r"[]]", "a]b"))
print(re.findall(r"[a\-c]", "a-c"))
print(re.findall(r"[\d\s]", "a1 b2"))
print(re.findall(r"[^\W\d]", "ab1_c"))
print(re.match(r"[\x41\x42]+", "ABC").group(0))

# ---- groups ----------------------------------------------------------------
m = re.search(r"(\d+)-(\d+)", "x 12-345 y")
print(m.group(), m.group(1), m.group(2), m.groups())
print(m.group(0, 2), m.span(1), m.start(2), m.end(2))
print(m[0], m[1])
print(m.pos, m.endpos, m.string)
print(m.re.pattern, m.re.groups)

m = re.match(r"(?P<who>\w+) (?P<what>\w+)", "ada wrote")
print(m.groupdict(), m.group("who"), m.group("what"))
print(m.re.groupindex["what"], m.lastgroup)

print(re.match(r"(?:ab)+", "ababab").group(0))
print(re.match(r"(?:ab)+", "ababab").groups())

# AN UNSET GROUP IS None, and its default is what `groups()` was given.
m = re.match(r"(a)|(b)", "b")
print(m.groups(), m.groups("!"), m.group(1), m.group(2))

# `lastindex` IS THE GROUP THAT CLOSED LAST, which nested groups distinguish
# from "the highest number that matched".
print(re.match(r"((a))", "a").lastindex)
print(re.match(r"(a)(b)", "ab").lastindex)
print(re.match(r"a", "a").lastindex)

# ---- backreferences --------------------------------------------------------
print(bool(re.match(r"(ab)\1", "abab")))
print(bool(re.match(r"(ab)\1", "abcd")))
print(re.findall(r"(\w)\1", "aabbc"))
print(bool(re.match(r"(?P<x>a)(?P=x)", "aa")))
# A GROUP THAT NEVER MATCHED FAILS THE REFERENCE rather than matching empty.
print(bool(re.match(r"(a)?\1", "")))

# ---- anchors and boundaries ------------------------------------------------
print(re.findall(r"^\w+", "one two"))
print(re.findall(r"^\w+", "one\ntwo", re.M))
print(re.findall(r"\w+$", "one\ntwo", re.M))
print(bool(re.search(r"a$", "a\n")))
print(bool(re.search(r"a\Z", "a\n")))
print(re.findall(r"\bcat\b", "cat category the cat."))
print(re.findall(r"\Bat\B", "cats batch at"))
print(re.sub(r"\b", "|", "ab cd"))

# ---- lookahead -------------------------------------------------------------
print(re.findall(r"\d+(?= dollars)", "10 dollars 20 euros"))
print(re.findall(r"\d+(?! dollars)", "10 dollars 20 euros"))
m = re.match(r"(?=(a+))a", "aaa")
print(m.group(0), m.group(1))
print(bool(re.match(r"(?!x)a", "a")), bool(re.match(r"(?!a)a", "a")))

# ---- alternation -----------------------------------------------------------
# LEFTMOST-FIRST AND NOT LONGEST: a backtracking engine takes the first branch
# that lets the rest through, which is a different answer, not a slower one.
print(re.match(r"a|ab", "ab").group(0))
print(re.match(r"(a|ab)c", "abc").group(0))
print(re.findall(r"cat|dog", "dog cat dog"))

# ---- flags -----------------------------------------------------------------
print(re.findall(r"[a-z]+", "AbC", re.I))
print(bool(re.match(r"a.c", "a\nc")), bool(re.match(r"a.c", "a\nc", re.S)))
print(re.match(r"""
    \d+     # the number
    \s*
    \w+     # the word
""", "42 apples", re.X).group(0))
print(re.match(r"(?i)abc", "ABC").group(0))
print(re.match(r"(?i:ab)c", "ABc").group(0))
print(bool(re.match(r"(?i:ab)c", "ABC")))
# `int(...)` BECAUSE CPYTHON'S FLAGS ARE AN `enum.IntFlag` AND THESE ARE
# PLAIN INTS: `re.I` prints as `re.IGNORECASE` there and as `2` here, which is
# a difference in `enum` rather than in `re` and is declared in the module.
# The VALUE is the part a pattern's behaviour depends on, so that is compared.
print(int(re.compile(r"(?i)x").flags & re.I))

# ---- findall, finditer -----------------------------------------------------
print(re.findall(r"\d", "a1b2"))
print(re.findall(r"(\d)(\w)", "1a 2b"))
print(re.findall(r"(a)?b", "b"))
print([m.span() for m in re.finditer(r"\d+", "1 22 333")])
# AN EMPTY MATCH ADVANCES BY ONE, so a starred pattern terminates and gives
# CPython's count rather than looping.
print(re.findall(r"x*", "axbxc"))

# ---- sub, subn, split ------------------------------------------------------
print(re.sub(r"\d+", "#", "a1b22c"))
print(re.subn(r"\d+", "#", "a1b22c"))
print(re.sub(r"\d+", "#", "a1b22c", count=1))
print(re.sub(r"(\w)(\d)", r"\2\1", "a1 b2"))
print(re.sub(r"(?P<c>\w)(\d)", r"\g<c>-\g<2>", "a1"))
print(re.sub(r"(a)?b", r"[\1]", "b"))
print(re.sub(r"\d", lambda m: "<" + m.group(0) + ">", "a1b2"))
print(re.sub(r"x*", "-", "axbxc"))
print(re.split(r",", "a,b,c"))
print(re.split(r",", "a,b,c", maxsplit=1))
print(re.split(r"(,)", "a,b"))
print(re.split(r"(,)?;", "a;b,;c"))
print(re.split(r"x*", "axbxc"))
print(re.split(r"\b", "a b"))

# ---- expand ----------------------------------------------------------------
m = re.match(r"(\w+) (\w+)", "hello world")
print(m.expand(r"\2 \1"))
print(m.expand(r"\g<2>-\g<1>"))
print(m.expand(r"a\nb"))

# ---- escape ----------------------------------------------------------------
print(re.escape("a.b*c+d?e"))
print(re.escape("a b-c#d"))
print(re.escape("plain_123"))
print(bool(re.match(re.escape("a.c") + "$", "a.c")))
print(bool(re.match(re.escape("a.c") + "$", "abc")))

# ---- Pattern -----------------------------------------------------------------
p = re.compile(r"(?P<n>\d+)")
print(p.pattern, p.groups, p.groupindex["n"])
print(p.search("ab 12").group(0))
print(p.match("12ab").group(0))
print(p.match("ab12"))
print(p.findall("1 22"))
print(p.sub("#", "1 22"))
print(p.split("a1b"))
print(re.compile(p) is p)
# `pos` AND `endpos` NARROW THE SUBJECT WITHOUT COPYING IT, and `^` still
# means the start of the string rather than the start of the slice.
print(p.search("12 34", 3).group(0))
print(re.compile(r"\d+").search("12345", 1, 3).group(0))

# ---- the errors ------------------------------------------------------------
# THE REFUSALS ARE NOT HERE. Lookbehind, atomic groups and possessive
# quantifiers are features CPython HAS, so a test that asserts asmpython
# refuses them is asserting a difference and cannot be a differential one.
# They are measured against asmpython alone, in
# `tests/asmpython/integration/test_stdlib.py::test_re_refuses_what_it_does_not_have`.
#
# What IS here is the patterns both refuse: an ordinary mistake is an error in
# CPython too, and a module that accepted one would be silently wrong.
for bad in (r"(", r"[a", r"a{3,2}", r"*a", r"\1", r"(?P<n>a)(?P<n>b)", r"\q"):
    try:
        re.compile(bad)
        print("ACCEPTED", bad)
    except re.error:
        print("rejected:", bad)

try:
    re.compile(r"a", re.I)
    re.compile(re.compile(r"a"), re.I)
except ValueError as exc:
    print("ValueError:", exc)

try:
    re.match(r"(a)", "a").group(2)
except IndexError:
    print("IndexError for a group that does not exist")

print("done")
