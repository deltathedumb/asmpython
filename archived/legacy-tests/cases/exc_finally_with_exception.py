# expect:
# finally ran
# caught
def f():
    try:
        raise ValueError('x')
    except ValueError:
        return 'caught'
    finally:
        print('finally ran')
print(f())
# asmpython (beta/3.14.0) MISMATCH: prints 'caught\n' (wrong).
