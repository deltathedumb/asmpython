import os
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import SemaAnalyzer

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
mod = Parser(toks).parse()
os.system("echo P1-parsed")
s = SemaAnalyzer(mod)
os.system("echo P2-ctor")
s.analyze()
os.system("echo P3-analyzed")
