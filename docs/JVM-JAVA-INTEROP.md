# Calling Java from compiled Python

Import a Java package under `java.` and construct its classes directly:

```python
import java
import java.util as ju
import java.com.google.gson as gson


def main():
    items = ju.ArrayList()
    java.jcall_s(items, "add", "hello")
    print(java.jcalls(items, "toString"))      # [hello]

    g = gson.Gson()
    print(java.jcalls_s(g, "toJson", "hello")) # "hello"


main()
```

`java.util` is the JDK's `java.util`. Anything else under `java.` has the
prefix stripped: `java.com.google.gson` is the package `com.google.gson`. The
leading `java.` is the marker saying "the rest of this is Java", which is what
lets the compiler stay out of it — see below.

## How the import resolves, without the core knowing about Java

The core gained one generic rule: **a registered binding module may answer for
its own subpaths.** A module's `BINDINGS` may carry a
`__resolve_submodule__(subpath)` callable, and `import a.b.c` asks `a` to
resolve `b.c` when nothing is registered under the full name.

That is what makes a namespace too large to enumerate importable. No registry
can list every class in `com.google.gson`, and asking the JVM is neither cheap
nor reliable — a package is not a closed set. But the module that *owns* the
namespace can resolve one on request.

Rooting it under `java.` is what keeps this honest. `import com.google.gson`
would require the compiler to guess that an unresolvable dotted import means
Java; `import java.com.google.gson` is a subpath of a module that is already
registered, so the rule names no namespace at all — whoever registered the
prefix decides.

A class attribute becomes a zero-argument constructor whose symbol carries the
class name (`__jvm_new$com.google.gson.Gson`), because the frontend emits
`call <name>` and has no way to attach a constant to a call. The backend splits
it back out at codegen. All the Java knowledge is on the backend side of that
symbol.

### What the sugar does not cover

- **A bare class reference.** `Gson = gson.Gson` does not work; only
  `gson.Gson()`. A class handle is obtained at runtime, and a bare attribute
  has no call to hang that on.
- **Constructors with arguments.** `ju.ArrayList()` is fine;
  `ju.ArrayList(10)` is not. A binding declares one fixed arity, and this
  mapping cannot know a class's constructors without loading it. Use
  `java.jnew_i(java.jclass("java.util.ArrayList"), 10)`.
- **Methods.** There is no `items.add("x")`; that would mean teaching the
  compiler what a Java object is.

## The explicit form

Everything above is sugar over these, which always work:

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
