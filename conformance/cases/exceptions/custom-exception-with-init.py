# tier: spec
# ref: library/exceptions.html#BaseException
# expect:
# 404
# 404: missing
# ('404: missing',)
class AppError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code

try:
    raise AppError(404, "missing")
except AppError as e:
    print(e.code)
    print(str(e))
    print(e.args)
