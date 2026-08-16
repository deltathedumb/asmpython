# expect:
# custom message
class AppError(Exception):
    def __str__(self):
        return 'custom message'
try:
    raise AppError()
except AppError as e:
    print(str(e))
# asmpython (beta/3.14.0) MISMATCH: prints '\n' (wrong).
