# expect:
# ['a', 'b']
log = []
def record(msg):
    log.append(msg)
record('a')
record('b')
print(log)
# asmpython (beta/3.14.0) MISMATCH: prints '[5368737792, 5368737794]\n' (wrong).
