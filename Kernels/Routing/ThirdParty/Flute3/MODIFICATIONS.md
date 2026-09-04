# Source modification record

The files beneath `Upstream/` are byte-for-byte copies from OpenROAD revision
`566a2df7ea55bb44c530ff0944b9f4b69b306a23`; no source or lookup-table changes
have been made.

RedstoneCompiler does not compile these files. The live Rust API uses the
native deterministic multi-source topology/path implementation. FLUTE3 stays
disabled because the audited source is not isolated from OpenROAD's `stt` and
`utl` runtime, so no legal-candidate or RCA4/CLA4 benefit can yet be measured.
