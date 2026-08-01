# tier: spec
# ref: reference/executionmodel.html#naming-and-binding
# expect:
# UnboundLocalError
# global
x = "global"

def read_then_assign():
    try:
        return x
    except UnboundLocalError:
        return "UnboundLocalError"
    x = "local"

print(read_then_assign())

def only_reads():
    return x

print(only_reads())
