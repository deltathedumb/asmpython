# probes: a starred sequence pattern captures the rest
# expect:
# (1, [2, 3])
# ('empty', [])
def head_tail(items):
    match items:
        case [first, *rest]:
            return (first, rest)
        case []:
            return ("empty", [])


print(head_tail([1, 2, 3]))
print(head_tail([]))
