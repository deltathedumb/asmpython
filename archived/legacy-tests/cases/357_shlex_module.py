# expect:
# 3
# hello
# world
# foo
# hello world
# 'hello world'

import shlex

tokens: list[str] = shlex.split("hello world foo")
print(len(tokens))
print(tokens[0])
print(tokens[1])
print(tokens[2])
tokens2: list[str] = shlex.split("'hello world'")
print(tokens2[0])
print(shlex.quote("hello world"))
