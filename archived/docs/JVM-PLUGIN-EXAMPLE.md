# A worked example: a Minecraft mod in Python

A complete NeoForge mod — one `.py` file, compiled to a jar, dropped in
`mods/`. No loader mod, no interpreter, no Java source. It registers a real
item through the real NeoForge registries.

This is here because it exercises everything at once: a host that *loads* a
class, an API reached through `java`, and a callback going the other way.

## The mod

```python
import java
import java.net.neoforged.neoforge.registries as registries

from asmpython import Public, access


@access(Public)
def make_bat():
    """Supplier<Item>: builds the Item NeoForge asks for during registration."""
    Properties = java.jclass("net.minecraft.world.item.Item$Properties")
    props = java.jnew(Properties)
    java.jcall_i(props, "stacksTo", 1)
    java.jcall_i(props, "durability", 250)

    Item = java.jclass("net.minecraft.world.item.Item")
    return java.jnew_o(Item, props)


@access(Public)
def on_construct(mod_bus):
    """NeoForge hands a mod its event bus; everything hangs off that."""
    DeferredRegister = java.jclass(
        "net.neoforged.neoforge.registries.DeferredRegister")
    items = java.jcall_s(DeferredRegister, "createItems", "demomod")

    supplier = java.jproxy("java.util.function.Supplier", "make_bat")
    java.jcall_so(items, "register", "bat", supplier)
    java.jcall_o(items, "register", mod_bus)
    return 0


def main():
    print("[demomod] python mod body running")
```

## The build

```console
asmpython build mod.py --backend jvm \
    --jvm-class demomod.DemoMod \
    --jvm-runtime-package demomod.rt \
    --jvm-instantiate "net.neoforged.bus.api.IEventBus" \
    --jvm-annotation "net.neoforged.fml.common.Mod(value=demomod)" \
    --jvm-resource "META-INF/neoforge.mods.toml=neoforge.mods.toml" \
    --jvm-resource "assets/demomod/models/item/bat.json=assets/bat.json" \
    --jvm-resource "assets/demomod/textures/item/bat.png=assets/bat.png" \
    --jvm-resource "assets/demomod/lang/en_us.json=assets/en_us.json" \
    -o demomod.jar
```

Nothing in that command is a Minecraft feature of the compiler. It is the
generic set from [JVM-HOSTED-JARS.md](JVM-HOSTED-JARS.md): name the class,
annotate it, make it constructible, relocate the runtime, carry some files.

## The three pieces that make it work

**`--jvm-instantiate "…IEventBus"`** — NeoForge constructs a mod class and
passes it the event bus. The declared parameter reaches an exported
`on_construct` as a handle. Without a way to receive it, code loaded this way
can only be told "you were loaded", never "here is what to use".

**`jproxy`** — `DeferredRegister.register` takes a `Supplier<Item>`. Compiled
code cannot hand Java a function, so this goes the other way: a
`java.lang.reflect.Proxy` implements the interface and forwards every call to
an exported Python function. Callback-shaped APIs are unreachable otherwise,
and most framework APIs are callback-shaped.

**`@access(Public)`** on both callbacks. Nothing inside the module calls them —
the framework does — so reachability analysis drops them and the generated
class has no such method. The hook then never fires and nothing says why.

## What this cost to get right

Two bugs worth knowing about, both found only by running it:

- **An exported function returns a BOXED value.** Its type is "any", so the
  lowering wraps it in a heap cell. A Python caller unboxes as a matter of
  course; Java reaching in through reflection just gets the cell, and every
  returned object looked like a meaningless address. The proxy unboxes now.
- **`StringBuilder` is a `CharSequence`.** Converting every CharSequence to a
  Python string meant a StringBuilder could not be held as an object at all.
  Only a real `String` converts.

## Verified

Loaded in a Minecraft 1.21.1 NeoForge client: the game lists the mod, the
Python body runs during construction, `demomod:bat` reaches the item registry,
and its model, texture and name resolve from the jar.
