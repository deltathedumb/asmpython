from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
mod = Parser(toks).parse()
print(len(mod.body))
print(len(mod.funcs))
