# expect:
# hello world
# 11
# 3
# hi there
# 4
# ab
# 0
# 1
# hXllX wXrld

import re

m = re.match(r"hello world", "hello world")
print(m.group(0))
print(m.end())

results = re.findall(r"\d+", "abc 12 def 34 ghi 56")
print(len(results))

s = re.sub(r"hello", "hi", "hello there")
print(s)

m2 = re.search(r"\d+", "abc 123 def")
print(m2.start())

m3 = re.search(r"[a-z]+", "123ab456")
ab: str = m3.group(0)
print(ab)

m4 = re.match(r"\d+", "hello")
print(0 if m4 is None else 1)

m5 = re.fullmatch(r"\d+", "123")
print(1 if m5 is not None else 0)

s2 = re.sub(r"[aeiou]", "X", "hello world")
print(s2)
