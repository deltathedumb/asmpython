from asmpython._compiler.lexer import Lexer

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
i = 0
while i < len(toks):
    t = toks[i]
    print(t.kind, t.value)
    i = i + 1
