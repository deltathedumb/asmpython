# expect:
# ['a', 'c']
log = {}
entries = [('err', 'a'), ('ok', 'b'), ('err', 'c')]
for level, msg in entries:
    log.setdefault(level, []).append(msg)
print(sorted(log['err']))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'append'
