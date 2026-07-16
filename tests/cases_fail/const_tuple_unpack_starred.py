# expect-error: cannot reassign const
extend constants
const X = 1
X, *rest = [1, 2, 3]
