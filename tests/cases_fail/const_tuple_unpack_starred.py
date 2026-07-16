# ext: constants
# expect-error: cannot reassign const
const X = 1
X, *rest = [1, 2, 3]
