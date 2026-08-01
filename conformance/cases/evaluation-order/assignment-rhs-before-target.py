# tier: spec
# ref: reference/simple_stmts.html#assignment-statements
# expect:
# ['value', 'key', ('set', 'key', 'value')]
log = []

def probe(n):
    log.append(n)
    return n

class Sink:
    def __setitem__(self, k, v):
        log.append(("set", k, v))

s = Sink()
s[probe("key")] = probe("value")
print(log)
