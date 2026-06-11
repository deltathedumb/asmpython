from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
try:
    mod = Parser(toks).parse()
    print("parsed body:", len(mod.body))
except Exception as e:
    print("ERR")
    msg: str = e.message
    print(msg)
