# expect:
# 2
# Hello
# world
# Hello world
# Hi [...]

import textwrap

lines: list[str] = textwrap.wrap("Hello world", 7)
print(len(lines))
print(lines[0])
print(lines[1])

filled: str = textwrap.fill("Hello world", 20)
print(filled)

short: str = textwrap.shorten("Hi there how are you", 10, " [...]")
print(short)
