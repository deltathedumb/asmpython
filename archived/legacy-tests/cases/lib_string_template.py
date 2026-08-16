# expect:
# x is 5
from string import Template
t = Template('$name is $age')
print(t.substitute(name='x', age=5))
# asmpython (beta/3.14.0) rejects at compile: [E021] substitute() got an unexpected keyword argument 'name'
