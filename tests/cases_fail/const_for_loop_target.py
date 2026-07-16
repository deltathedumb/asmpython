# expect-error: cannot reassign const
extend constants
const X = 1
for X in range(3):
    pass
