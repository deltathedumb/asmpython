# probes: typing.Optional exposes its arguments
# expect:
# (<class 'int'>, <class 'NoneType'>)
import typing

print(typing.get_args(typing.Optional[int]))
