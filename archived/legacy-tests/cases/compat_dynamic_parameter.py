# guards: dynamic_parameter_compat_fixes
# expect:
# abc
# HI
def join_all(parts):
    out = ""
    for p in parts:
        out = out + p
    return out


def call_it(fn, arg):
    return fn(arg)


def shout(text):
    return text.upper()


print(join_all(["a", "b", "c"]))
print(call_it(shout, "hi"))
