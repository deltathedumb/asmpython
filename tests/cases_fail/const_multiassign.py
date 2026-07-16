# expect-error: cannot reassign const
extend constants
const X = 1
X = Y = 2
