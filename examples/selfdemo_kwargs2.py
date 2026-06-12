from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
mod = Parser(toks).parse()
s = mod.body[0]
e = s.expr
ar = e.args
n = 0
for item in ar:
    n = n + 1
print("args count:", n)
kw = e.kwargs
m = 0
for item2 in kw:
    m = m + 1
print("kw count:", m)
