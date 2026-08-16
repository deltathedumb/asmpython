# expect:
# start started
# stop stopped
actions = {'start': lambda: 'started', 'stop': lambda: 'stopped'}
for name in sorted(actions):
    print(name, actions[name]())
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method '__call__'
