# probes: a custom exception carries its own fields
# expect:
# 404
# status 404
class HttpError(Exception):
    def __init__(self, status):
        super().__init__("status " + str(status))
        self.status = status


try:
    raise HttpError(404)
except HttpError as err:
    print(err.status)
    print(str(err))
