# expect:
# 00000000-0000-0000-0000-000000000000
import uuid
u = uuid.UUID(int=0)
print(str(u))
# asmpython (beta/3.14.0) rejects at compile: [E021] UUID() got an unexpected keyword argument 'int'
