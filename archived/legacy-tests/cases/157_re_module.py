# expect:
# hello
# 0
# 5
# world
# ['cat', 'bat', 'hat']
# hi WORLD there
# True
# 0

import re

m = re.match(r"hello", "hello world")
print(m.group(0))
print(m.start())
print(m.end())

s = re.search(r"world", "hello world")
print(s.group(0))

words: list[str] = re.findall(r"[cbh]at", "the cat sat on the bat and hat")
print(words)

result = re.sub(r"world", "WORLD", "hi world there")
print(result)

print(re.fullmatch(r"\d+", "12345") != None)
print(re.match(r"\d+", "hello"))
