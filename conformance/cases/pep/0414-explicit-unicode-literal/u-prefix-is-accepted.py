# tier: spec
# ref: reference/lexical_analysis.html#string-and-bytes-literals
# expect:
# abc str
# True True
# upper-prefix
# accepted SyntaxError accepted
s = u"abc"
print(s, type(s).__name__)
print(s == "abc", s is not None)
print(U"upper-prefix")
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    return "accepted"
print(check('u"x"'), check('ur"x"'), check('rb"x"'))
