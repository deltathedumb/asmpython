# expect:
# 6 5
def cmd_add(args):
    return sum(args)
def cmd_max(args):
    return max(args)
commands = {'add': cmd_add, 'max': cmd_max}
def run(name, args):
    return commands[name](args)
print(run('add', [1, 2, 3]), run('max', [4, 1, 5]))
# asmpython (beta/3.14.0) MISMATCH: prints '0 0\n' (wrong).
