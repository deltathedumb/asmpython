from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
try:
    mod = Parser(toks).parse()
    analyze(mod)
    print("sema ok")
except Exception as e:
    print("ERR")
    msg: str = e.message
    print(msg)
