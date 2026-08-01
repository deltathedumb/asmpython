# tier: spec
# ref: reference/compound_stmts.html#class-definitions
# expect:
# accepted | @deco\ndef f(): pass
# accepted | @deco\nclass C: pass
# SyntaxError | @deco
# accepted | class C(Base, metaclass=M): pass
# accepted | class C(*bases): pass
# accepted | class C(**kw): pass
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "@deco\ndef f(): pass",
    "@deco\nclass C: pass",
    "@deco",
    "class C(Base, metaclass=M): pass",
    "class C(*bases): pass",
    "class C(**kw): pass",
):
    print(check(src), "|", src.replace("\n", "\\n"))
