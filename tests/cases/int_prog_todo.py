# expect:
# ['b']
class TodoList:
    def __init__(self):
        self.items = []
    def add(self, task):
        self.items.append({'task': task, 'done': False})
    def complete(self, idx):
        self.items[idx]['done'] = True
    def pending(self):
        return [i['task'] for i in self.items if not i['done']]
t = TodoList()
t.add('a')
t.add('b')
t.complete(0)
print(t.pending())
# asmpython (beta/3.14.0) MISMATCH: prints '[5368741942]\n' (wrong).
