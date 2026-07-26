# expect:
# 14
def eval_postfix(tokens):
    stack = []
    for t in tokens:
        if isinstance(t, int):
            stack.append(t)
        else:
            b, a = stack.pop(), stack.pop()
            if t == '+':
                stack.append(a + b)
            elif t == '*':
                stack.append(a * b)
    return stack[0]
print(eval_postfix([3, 4, '+', 2, '*']))
# asmpython (beta/3.14.0) rejects at compile: [E051] mixed list element types (int and str); mixed-type lists need a tagged-value runtime, not yet implemented
