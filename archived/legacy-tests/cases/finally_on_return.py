# expect:
# cleanup
# 1
def f():
    try:
        return 1
    finally:
        print('cleanup')


print(f())
# a finally block does not run when the try returns; asmpython skips 'cleanup'.
