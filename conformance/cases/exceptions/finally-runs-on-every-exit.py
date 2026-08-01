# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# fin-ret
# ret
# fin-brk
# brk
# [1, 2]
def r():
    try:
        return 'ret'
    finally:
        print('fin-ret')

def b():
    for i in [1, 2]:
        try:
            break
        finally:
            print('fin-brk')
    return 'brk'

def c():
    out = []
    for i in [1, 2]:
        try:
            continue
        finally:
            out.append(i)
    return out

print(r())
print(b())
print(c())
