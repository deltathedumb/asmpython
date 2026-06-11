"""Self-hosting demo: the asmpython compiler, compiled by itself, compiling.

This program — written in asmpython's compilable subset — embeds a tiny source
string and runs it through the real pipeline (Lexer -> Parser -> sema ->
WindowsCodegen), printing the generated NASM. Compiling THIS file with the
CPython-hosted compiler produces a native executable that IS the compiler
(front end + codegen); running it must print assembly that itself assembles,
links, and runs. That round trip is the self-hosting proof the codegen probe
(which only checks .asm emission) can't give.
"""

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze
from asmpython._compiler.target_windows import WindowsCodegen

src = 'print(1 + 2)\n'
toks = Lexer(src).tokenize()
mod = Parser(toks).parse()
analyze(mod)
gen = WindowsCodegen(mod)
print(gen.generate())
