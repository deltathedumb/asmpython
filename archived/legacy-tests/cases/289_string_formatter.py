# expect:
# hello world
# hello world

from string import Formatter, Template

f = Formatter()
print(f.format("{0} {1}", "hello", "world"))

t = Template("$word")
result: str = t.substitute(0, "world", "word")
print("hello " + result)
