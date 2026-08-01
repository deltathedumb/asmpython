# tier: spec
# ref: library/annotationlib.html
# min-python: 3.14
# expect:
# ['return', 'x', 'y']
# Undefined
# True
# True
import annotationlib

def f(x: int, y: "Undefined") -> bool:
    return True

print(sorted(annotationlib.get_annotations(f, format=annotationlib.Format.STRING)))
print(annotationlib.get_annotations(f, format=annotationlib.Format.STRING)["y"])
print(annotationlib.Format.VALUE.value <= annotationlib.Format.STRING.value)
print(hasattr(f, "__annotate__"))
