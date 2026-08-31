# Adding standard cells

Standard-cell geometry is authoritative physical behavior. Add or change cells
under `Compiler/Cells` and keep their placement, routing access, writer, and
Fabric-server contracts aligned.

A cell definition must provide deterministic dimensions, block geometry,
named pins, legal orientations, access directions, and all support, air, and
electrical claims. Pin cardinality must match the logical gate contract.

Add tests for every orientation, pin location, electrical isolation, claim
ownership, rendering, and server-observed behavior. A visually plausible
template is not accepted until the resource graph, emitted blocks, and Fabric
server agree.
