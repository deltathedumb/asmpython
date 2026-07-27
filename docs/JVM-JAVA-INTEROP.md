# Calling Java from compiled Python

The JVM backend contributes a `java` module. Import it and you can reach any
class the host can see:

```python
import java


def main():
    ArrayList = java.jclass("java.util.ArrayList")
    items = java.jnew(ArrayList)
    java.jcall_s(items, "add", "hello")
    java.jcall_s(items, "add", "world")
    print(java.jcall(items, "size"))        # 2
    print(java.jcalls(items, "toString"))   # [hello, world]

    Math = java.jclass("java.lang.Math")
    print(java.jcall_ii(Math, "max", 17, 42))   # 42


main()
```

## Where it lives, and why

The module is declared by the backend
(`asmpython/_backends/jvm/bindings.py`), not by asmpython's stdlib. Calling
Java is meaningful to this backend and meaningless to x86-64, so the core has
no business shipping a module for it. What the core offers is a registry —
`asmpython.stdlib.register_bindings(name, bindings)` — and it knows nothing
about who fills it. Compile the same file for another backend and `import java`
is simply an unknown module.

A **host** can register its own module the same way, without it living in
asmpython at all:

```console
asmpython build mod.py --backend jvm --bindings mymodule=path/to/mymodule.py
```

The file declares a `BINDINGS` dict of `Func(arg_types=..., ret_type=...,
c_name=...)`, exactly like the stdlib's own modules, and `import mymodule` then
resolves to it.

## Why the argument shapes are in the names

asmpython's FFI declares a fixed arity and a type per argument, so no single
binding can describe a variadic call. The names carry the shape instead:

| suffix | argument |
| --- | --- |
| `_s` | a string |
| `_i` | an int |
| `_o` | a handle (another Java object) |

and a leading `s` on the *call* means it returns a string:

```python
java.jcall(obj, "size")             # -> word
java.jcalls(obj, "toString")        # -> str
java.jcall_si(obj, "insert", "x", 3)
```

This is the same thing JNI does with `CallIntMethod` / `CallObjectMethod`, and
for the same reason: a 64-bit word cannot say whether it is the number 5 or the
address of a string, and the caller is the only one who knows.

Sugaring this into `items.add("hello")` would mean teaching the compiler what a
Java object is — backend knowledge in the core, which is exactly what this
arrangement exists to avoid.

## Handles, strings and numbers

Compiled code holds no JVM references; every value it has is a 64-bit word. So
a Java object is a **handle** into a table the runtime keeps, and handles start
above the heap so mistaking one for an address is a lookup miss rather than a
silent read of unrelated memory.

Values crossing back are converted rather than handled where there is an
obvious Python equivalent:

| Java returns | Python gets |
| --- | --- |
| `String`, `char` | a str |
| `int`/`long`/`short`/`byte` | an int |
| `boolean` | 0 or 1 |
| `float`/`double` | a float |
| anything else | a handle |

Because a returned `String` becomes a Python string rather than a handle, you
can keep using it as a Java receiver anyway — a heap address passed where a
handle is expected is read back as a `java.lang.String`:

```python
text = java.jcalls(sb, "toString")
print(java.jcalls(text, "toUpperCase"))
```

## Overloads

Resolution scores every candidate of the right name and arity and takes the
best fit: the type Python actually has beats a lossy conversion. That matters
more than it sounds. `getMethods()` has no defined order, and Python has one
integer type where Java has six — first-match resolution sends
`Math.max(17, 42)` to `max(double, double)` about half the time and returns
42.0 as a bit pattern, which is a wrong answer with no error.

Where two overloads genuinely tie, name the descriptor:

```python
java.callExact(obj, "valueOf", "(I)Ljava/lang/String;", ...)
```

## A caveat worth knowing

An import asmpython cannot resolve does **not** fail the build. The calls
through it compile to nothing, and the program runs and does less than it
should. So if a `java.*` call seems to have no effect, check that the backend
is `jvm` — the same silence is what you get from `import com.google.gson`,
which is not a module asmpython knows.
