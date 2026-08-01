# tier: spec
# ref: reference/lexical_analysis.html#indentation
# expect:
# nested
# IndentationError
# IndentationError
def f():
    if True:
        return "nested"
    return "flat"

print(f())
try:
    compile("def g():\nreturn 1", "<t>", "exec")
except IndentationError:
    print("IndentationError")
try:
    compile("if True:\n  a = 1\n   b = 2", "<t>", "exec")
except IndentationError:
    print("IndentationError")
