# expect:
# 404 code 404
class MyErr(Exception):
    def __init__(self, code):
        super().__init__('code ' + str(code))
        self.code = code
try:
    raise MyErr(404)
except MyErr as e:
    print(e.code, str(e))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
