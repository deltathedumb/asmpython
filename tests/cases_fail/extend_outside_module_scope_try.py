# expect-error: only appear at module scope
try:
    extend constants
except Exception:
    pass
