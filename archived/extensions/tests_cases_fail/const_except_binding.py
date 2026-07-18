# ext: constants
# expect-error: cannot reassign const
const X = 1
try:
    pass
except Exception as X:
    pass
