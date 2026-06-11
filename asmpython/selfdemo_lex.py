from asmpython._compiler.lexer import Lexer

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
print(len(toks))
