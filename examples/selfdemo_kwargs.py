from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
mod = Parser(toks).parse()
s = mod.body[0]
e = s.expr
ar = e.args
print("args raw:", ar)
print("args len:", len(ar))
kw = e.kwargs
print("kw raw:", kw)
print("kw len:", len(kw))
