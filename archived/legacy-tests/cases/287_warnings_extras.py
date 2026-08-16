# expect:
# entered
# exited

from warnings import catch_warnings, formatwarning, showwarning

cw = catch_warnings()
cw.__enter__()
print("entered")
cw.__exit__(0, 0, 0)
print("exited")
