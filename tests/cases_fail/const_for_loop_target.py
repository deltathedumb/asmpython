# ext: constants
# expect-error: cannot reassign const
const X = 1
for X in range(3):
    pass
