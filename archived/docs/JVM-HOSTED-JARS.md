# Building a jar a host will load

Most compiled output is *run*: something calls `main`. A plugin jar is
different — it is **loaded**. A host scans for a class, reads its annotations,
constructs it, and calls into it. Nothing calls `main` at all.

Five options shape a jar for that. None of them names a framework; the backend
emits what it is told to and knows nothing about who will load the result.

| option | what it does |
| --- | --- |
| `--jvm-class NAME` | name the generated class |
| `--jvm-annotation A(k=v)` | add a runtime-visible class annotation (repeatable) |
| `--jvm-instantiate` | emit a public no-arg constructor that runs the module body |
| `--jvm-resource PATH=FILE` | carry a file in the jar at `PATH` (repeatable) |
| `--jvm-runtime-package PKG` | compile the bundled runtime into `PKG` |

## Why each one exists

**`--jvm-annotation`** — a host that discovers classes does it by annotation.
Written the way the host's documentation writes it, not as a class-file
descriptor:

```text
com.example.Plugin
com.example.Plugin(value=demo)
com.example.Plugin(value=demo, category=tools)
```

String elements only. That covers the marker annotations used to *find* a
class, which is the reason generated code needs one; anything richer wants a
hand-written Java class.

**`--jvm-instantiate`** — a host that constructs its entry point never calls
`main`, so the module body would never run. The constructor runs it, which
makes "the host made an instance" and "the Python ran" the same event. It also
makes the class public and non-final, since a final class with no constructor
cannot be instantiated.

**`--jvm-resource`** — a jar is usually only loadable when it also carries the
descriptor the host reads. Naming the archive path keeps that the caller's
decision rather than a layout invented here.

**`--jvm-runtime-package`** — the one that is easy to skip and then hard to
diagnose. Every compiled jar bundles asmpython's runtime. Two jars carrying it
under the same package are a **split package**, which the Java module system
rejects before anything runs:

```text
java.lang.module.ResolutionException: Modules demomod and asmpython.jvm.runtime
export package asmpython.jvm to module minecraft
```

Relocating gives each jar its own copy under its own package, which is what
makes a self-contained jar actually self-contained. Use it whenever a host may
load more than one compiled jar — which, for a plugin, is always.

## Worked example: a Minecraft mod

A NeoForge mod is a jar with a `META-INF/neoforge.mods.toml` and a class
annotated `@Mod`. Both are options here; nothing about Minecraft is built in.

`mod.py`:

```python
from asmpython import Public, access


@access(Public)
def main():
    print("[demo] a mod written in Python, compiled to a jar")
```

`neoforge.mods.toml`:

```toml
modLoader = "javafml"
loaderVersion = "[4,)"
license = "MIT"

[[mods]]
modId = "demomod"
version = "1.0.0"
displayName = "Demo Mod (Python, compiled)"
```

```console
asmpython build mod.py --backend jvm \
    --jvm-class demomod.DemoMod \
    --jvm-instantiate \
    --jvm-runtime-package demomod.rt \
    --jvm-annotation "net.neoforged.fml.common.Mod(value=demomod)" \
    --jvm-resource "META-INF/neoforge.mods.toml=neoforge.mods.toml" \
    -o demomod.jar
```

Drop `demomod.jar` in `mods/`. The whole jar is:

```text
demomod/DemoMod.class
demomod/rt/{Memory,Containers,Runtime}.class
META-INF/neoforge.mods.toml
META-INF/MANIFEST.MF
```

Its only external reference is `java/lang/Object`. Verified loading in a
Minecraft 1.21.1 NeoForge client: the game lists it as a mod, the Python body
runs during construction, and the resource manager treats it as a first-class
mod alongside the rest.

## Calling the host back

`@access(Public)` is doing real work in that example, and it is worth
understanding before writing anything larger. A host calls *into* the jar, and
nothing inside the module calls those functions — so reachability analysis
drops them and the generated class simply has no such method. The hook then
never fires and nothing says why. Mark every host-called entry point:

```python
@access(Public)
def on_event(a, b):
    ...
```

Arguments and returns are 64-bit words, because compiled code holds no JVM
references: a string is an address in the runtime's heap
(`Runtime.readString` / `allocateString` convert), and an object is whatever
handle the host chose to pass.

To give the compiled code an API, point `--jvm-runtime` at a class that extends
the bundled `Runtime` and adds static methods. `invokestatic` resolves
inherited statics, so unresolved calls in the Python land on the host's methods
with the compiler knowing nothing about them. See `asmpython/stdlib/mcjvm.py`
for how such an API is declared on the Python side.
