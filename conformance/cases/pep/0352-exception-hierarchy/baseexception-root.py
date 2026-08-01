# tier: spec
# ref: library/exceptions.html#exception-hierarchy
# expect:
# BaseException
# True
# SystemExit False
# KeyboardInterrupt False
# GeneratorExit False
# True
print(Exception.__bases__[0].__name__)
print(issubclass(Exception, BaseException))
for exc in (SystemExit, KeyboardInterrupt, GeneratorExit):
    print(exc.__name__, issubclass(exc, Exception))
print(issubclass(StopIteration, Exception))
