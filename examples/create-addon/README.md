# Extending Create, from Python

A [Create](https://github.com/Creators-of-Create/Create) addon written in
Python and compiled to a mod jar. It makes magma blocks heat a steam boiler.

Verified against **Create 6.0.10 for Minecraft 1.21.1** (NeoForge): the block
registers into Create's own registry, and a magma block under a boiler heats
it in game.

## Why this one works

Create 6's `BoilerHeater` is a single-method **interface**:

```java
public interface BoilerHeater {
    SimpleRegistry<Block, BoilerHeater> REGISTRY;
    float getHeat(Level level, BlockPos pos, BlockState state);
}
```

Compiled Python cannot subclass a Java class, so anything requiring a
`Block` or `BlockEntity` subclass is out of reach. An interface is not:
`jproxy` builds one whose methods call an exported Python function. Create's
registry then holds a Python implementation like any other.

That is the general rule for picking an extension point — look for an
interface and a registry, not a base class.

## The whole addon

```python
@access(Public)
def magma_heat(level, pos, state):
    return 2.0


@access(Public)
def on_construct(mod_bus):
    BoilerHeater = java.jclass("com.simibubi.create.api.boiler.BoilerHeater")
    registry = java.jfield(BoilerHeater, "REGISTRY")

    Blocks = java.jclass("net.minecraft.world.level.block.Blocks")
    magma = java.jfield(Blocks, "MAGMA_BLOCK")

    heater = java.jproxy("com.simibubi.create.api.boiler.BoilerHeater", "magma_heat")
    java.jcall_oo(registry, "register", magma, heater)
```

In the log:

```text
[createpy] extending Create 6.0.10
[createpy] magma_block registered as a boiler heater: $Proxy61
```

Reading the value back out of Create's registry is the first check:
`$Proxy61` is the Python implementation, so the registration landed rather
than merely not throwing. The second is the game itself -- a boiler over magma
blocks heats, which means Create called into Python for the heat level and got
a number back.

## The one thing that made this hard

Reflection resolves a method on the receiver's CONCRETE class, and Create's
registry object is a `SimpleRegistryImpl$SingleImpl` — public, but in a
package its module does not export. Calling it from another module fails:

```text
IllegalAccessException: class createpy.rt.Java (in module createpy) cannot
access a member of class com.simibubi.create.impl.registry.SimpleRegistryImpl$SingleImpl
(in module create)
```

The runtime now retries such a call against the supertypes, where the same
method is declared on an exported interface. Most real APIs hide their
implementations this way, so without it very little of any modular mod would
be reachable.
