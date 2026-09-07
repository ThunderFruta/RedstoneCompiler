# Redstone Compiler

This project uses Minecraft to visualize PCB-style and logic-gate routing. It
compiles small SystemVerilog circuits into NAND gates, places and connects those
gates with redstone, tests the result, and exports it as a `.litematic` file.

## What it does

- Converts supported SystemVerilog into NAND-gate logic.
- Places the gates and routes the redstone connections.
- Checks the physical circuit with MCHPRS and a Fabric Minecraft server.
- Produces a Litematica schematic and diagnostic files.

## What I want it to do...

- Support for Redstone displays.
- Support for more redstone blocks.
- Bigger circuits without blowing up computation or faling routing.

## What it could be used for

- Visualizing how logic gates are placed and connected.
- Testing PCB-style placement and routing methods in a Minecraft grid.
- Generating redstone versions of small digital circuits.
- Comparing routing methods and their results.

The current input format supports one module, single-bit signals, `assign`
statements, and the `~`, `&`, `^`, and `|` operators. It does not currently
support vectors, stored state, module instances, constants, or `always` blocks.

## Build and test environment

The project was built and tested with:

- Ubuntu 24.04 on x86-64 Linux
- Python 3.12.3
- Rust 1.96.0 and Cargo 1.96.0
- OpenJDK 25.0.4
- Gradle 9.5.1
- Minecraft and Fabric 26.2

Setup and usage instructions are in [the documentation](Docs/Readme.md).
