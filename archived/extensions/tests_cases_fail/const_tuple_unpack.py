# ext: constants
# expect-error: cannot reassign const
const X = 1
X, y = (2, 3)
