# expect:
# [('retries', 3), ('timeout', 30)]
defaults = {'timeout': 30}
overrides = {'retries': 3}
def configure(**opts):
    return sorted(opts.items())
print(configure(**defaults, **overrides))
# asmpython (beta/3.14.0) rejects at compile: [E021] call takes at most one **expr argument
