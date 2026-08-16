# expect:
# Hello
# world
# Hello world

from textwrap import wrap, fill

lines = wrap("Hello world", 6)
print(lines[0])
print(lines[1])

result = fill("Hello world", 20)
print(result)
