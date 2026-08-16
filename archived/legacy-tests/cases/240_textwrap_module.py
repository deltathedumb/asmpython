# expect:
# 2
# The quick
# brown fox
# hello...

import textwrap

lines: list[str] = textwrap.wrap("The quick brown fox", 10)
print(len(lines))
l0: str = lines[0]
l1: str = lines[1]
print(l0)
print(l1)

d: str = textwrap.shorten("hello world", 10, placeholder="...")
print(d)
