# expect-error: cannot reassign const
extend constants
const X = 1
X, y = (2, 3)
