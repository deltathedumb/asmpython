# COVERAGE: TextWrapper -- width, initial_indent, subsequent_indent,
# expand_tabs, replace_whitespace, fix_sentence_endings, break_long_words,
# drop_whitespace, break_on_hyphens, tabsize, max_lines, placeholder -- and
# its wrap/fill methods; the module functions wrap, fill, shorten, dedent,
# indent.
#
# Run under CPython and under uasm; the outputs must be identical. So
# the assertions below are written against what the module IS SPECIFIED to
# do, not against what uasm currently does.
import textwrap

PROSE = ("Beautiful is better than ugly. Explicit is better than implicit. "
         "Simple is better than complex, and complex is better than "
         "complicated. Readability counts.")

# wrap/fill on ordinary prose, at several widths -- including one wider than
# the whole paragraph, which should come back as a single line.
for width in (10, 20, 40, 70, 1000):
    print(width, textwrap.wrap(PROSE, width))
    print(width, repr(textwrap.fill(PROSE, width)))

# MULTI-PARAGRAPH: wrap()/fill() treat the whole argument as ONE paragraph,
# so a blank line inside it is just more whitespace to collapse -- a caller
# who wants paragraphs kept apart has to split on them first.
MULTI = "First paragraph goes here.\n\nSecond paragraph goes here too."
print(textwrap.wrap(MULTI, 20))
print(repr(textwrap.fill(MULTI, 20)))

# initial_indent / subsequent_indent, which count toward the line width.
print(textwrap.wrap(PROSE, 30, initial_indent="* ", subsequent_indent="  "))
print(repr(textwrap.fill(PROSE, 24, initial_indent="    ")))

# break_long_words on (the default) and off, with a word longer than width
# in both directions -- wider than the whole width, and only a little over.
LONG = "a supercalifragilisticexpialidocious word"
print(textwrap.wrap(LONG, 10))
print(textwrap.wrap(LONG, 10, break_long_words=False))
print(textwrap.wrap(LONG, 5, break_long_words=False))
print(textwrap.wrap("short reallylongwordthatoverflows end", 12,
                     break_long_words=False))

# break_on_hyphens: a compound that breaks after the hyphen (two letters
# each side), one that does not (a single leading letter, like "x-ray"), and
# the em-dash case from CPython's own docstring example.
HYPH = "The well-known co-operative-minded x-ray technician left."
print(textwrap.wrap(HYPH, 20))
print(textwrap.wrap(HYPH, 20, break_on_hyphens=False))
print(textwrap.wrap("Look, goof-ball -- use the -b option!", 12))

# drop_whitespace, on text with leading/trailing/interior runs of spaces.
SPACY = "   lots   of   space   here   "
print(textwrap.wrap(SPACY, 10))
print(textwrap.wrap(SPACY, 10, drop_whitespace=False))

# max_lines + placeholder: text that fits within max_lines needs no
# placeholder at all, and text that overflows gets truncated with one --
# the default, and a custom one.
print(textwrap.wrap("one two three", 20, max_lines=3))
print(textwrap.wrap(PROSE, 20, max_lines=2))
print(textwrap.wrap(PROSE, 20, max_lines=2, placeholder=" [more]"))
print(textwrap.fill(PROSE, 30, max_lines=1))
print(textwrap.wrap(PROSE, 15, max_lines=4))

# fix_sentence_endings -- off (the default) leaves a single space after
# "one.", on doubles it.
SENT = "End of one. Start of two."
print(textwrap.wrap(SENT, 70))
print(textwrap.wrap(SENT, 70, fix_sentence_endings=True))

# expand_tabs / tabsize, and replace_whitespace off (a tab then stays a
# single character rather than becoming spaces at all).
print(textwrap.wrap("a\tb\tc", 70))
print(textwrap.wrap("a\tb\tc", 70, tabsize=4))
print(textwrap.wrap("a\tb", 70, expand_tabs=False, replace_whitespace=False))

# shorten: collapses internal whitespace first, then either fits as-is or
# truncates with a placeholder -- default and custom.
print(textwrap.shorten("Hello  world!", width=12))
print(textwrap.shorten("Hello  world!", width=11))
print(textwrap.shorten("  lots   of\n\textra   whitespace  ", width=15))
LAZY = "The quick brown fox jumps over the lazy dog"
print(textwrap.shorten(LAZY, width=20))
print(textwrap.shorten(LAZY, width=20, placeholder="..."))

# dedent: mixed indentation, a blank line, and a whitespace-only line that
# does NOT count toward the common margin (CPython's own rule) -- so it
# cannot drag the margin down to zero the way a real "    " prefix would.
TEXT = "    Hello there.\n      This is indented.\n\n    \n    Bye."
print(repr(textwrap.dedent(TEXT)))

# tabs and spaces are both whitespace but are not interchangeable margin
# characters -- "\thello" and "  hello" share no common leading whitespace.
MIXED_KIND = "\thello\n  world"
print(repr(textwrap.dedent(MIXED_KIND)))

TABS = "\thello\n\tworld\n\t\tnested"
print(repr(textwrap.dedent(TABS)))

# a whitespace-only line shorter than the common margin must not shrink it,
# because it is excluded from the computation entirely.
SHORT_BLANK = "    one\n \n    two\n"
print(repr(textwrap.dedent(SHORT_BLANK)))

NO_COMMON = "  one\ntwo\n"
print(repr(textwrap.dedent(NO_COMMON)))

# indent, with the real default predicate: a blank (or whitespace-only)
# line is left alone rather than prefixed -- and a custom predicate that
# only tags lines mentioning "two".
BODY = "line one\n\nline two\n   \nline three"
print(repr(textwrap.indent(BODY, "> ")))
print(repr(textwrap.indent(BODY, "> ", predicate=lambda line: True)))
print(repr(textwrap.indent(BODY, "> ", predicate=lambda line: "two" in line)))
