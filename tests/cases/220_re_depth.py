# expect:
# hello
# cat
# bat
# h-ll- w-rld
# hello
# world
# 6
# 11

import re

m = re.search(r"hello", "say hello world")
print(m.group(0))

results: list[str] = re.findall(r"[cb]at", "the cat and the bat")
print(results[0])
print(results[1])

replaced: str = re.sub(r"[aeiou]", "-", "hello world")
print(replaced)

parts: list[str] = re.split(r" ", "hello world")
print(parts[0])
print(parts[1])

m2 = re.search(r"world", "hello world")
print(m2.start())
print(m2.end())
