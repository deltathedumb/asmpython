# expect-error: cannot reassign const
extend constants
const X = 1
try:
    pass
except Exception as X:
    pass
