# expect-error: 'NotAContextManager' object does not support the context manager protocol (missing __enter__/__exit__)
class NotAContextManager:
    def __init__(self, name):
        self.name = name


with NotAContextManager("x") as cm:
    print(cm.name)
