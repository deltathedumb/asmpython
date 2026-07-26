# expect:
# True False
def balanced(s):
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    for ch in s:
        if ch in '([{':
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return len(stack) == 0
print(balanced('([]{})'), balanced('(]'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
