# expect:
# caught ValueError
try:
    raise ValueError('v')
except Exception as e:
    print('caught', type(e).__name__)
# asmpython (beta/3.14.0) MISMATCH: prints 'caught str\n' (wrong).
