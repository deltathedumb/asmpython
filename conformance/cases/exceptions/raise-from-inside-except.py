# tier: spec
# ref: reference/simple_stmts.html#the-raise-statement
# expect:
# KeyError
# 'outer'
try:
    try:
        raise ValueError('inner')
    except ValueError:
        raise KeyError('outer')
except KeyError as e:
    print(type(e).__name__)
    print(str(e))
