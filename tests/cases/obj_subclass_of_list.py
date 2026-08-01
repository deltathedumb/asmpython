# probes: a list subclass inherits list behaviour
# expect:
# 2
# 1
# [1, 2]
class Stack(list):
    def push(self, value):
        self.append(value)
        return self


s = Stack()
s.push(1).push(2)
print(len(s))
print(s[0])
print(list(s))
