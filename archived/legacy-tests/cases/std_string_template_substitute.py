# probes: string.Template.substitute takes keywords
# expect:
# Hi, Ada!
import string

print(string.Template("$greet, $name!").substitute(greet="Hi", name="Ada"))
