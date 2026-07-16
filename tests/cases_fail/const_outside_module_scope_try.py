# ext: constants
# expect-error: only appear at module scope
try:
    const X = 1
except Exception:
    pass
