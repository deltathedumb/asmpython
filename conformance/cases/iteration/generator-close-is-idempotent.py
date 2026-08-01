# tier: spec
# ref: reference/expressions.html#generator.close
# expect:
# ['exit', 'finally']
# StopIteration
log = []

def gen():
    try:
        yield 1
    except GeneratorExit:
        log.append("exit")
        raise
    finally:
        log.append("finally")

g = gen()
next(g)
g.close()
g.close()
print(log)
try:
    next(g)
except StopIteration:
    print("StopIteration")
