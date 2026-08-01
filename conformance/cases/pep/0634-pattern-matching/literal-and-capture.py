# tier: spec
# ref: peps.python.org/pep-0634/
# expect:
# zero
# one-list 5
# pair 3
# mapping 9
# str hi
# other
def kind(v):
    match v:
        case 0:
            return 'zero'
        case [x]:
            return 'one-list ' + str(x)
        case [x, y]:
            return 'pair ' + str(x + y)
        case {'k': val}:
            return 'mapping ' + str(val)
        case str() as s:
            return 'str ' + s
        case _:
            return 'other'

for v in [0, [5], [1, 2], {'k': 9}, 'hi', 3.5]:
    print(kind(v))
