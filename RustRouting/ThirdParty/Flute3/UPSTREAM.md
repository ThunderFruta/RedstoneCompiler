# FLUTE3 upstream record

- Upstream: `https://github.com/The-OpenROAD-Project/OpenROAD`
- Audited revision: `566a2df7ea55bb44c530ff0944b9f4b69b306a23`
- Upstream path: `src/stt/src/flt`
- License: BSD-3-Clause; copied in `LICENSE`

The pinned `flute.cpp`, README, `POWV9.dat`, and `POST9.dat` are retained below
`Upstream/`. The C++ file includes OpenROAD `stt` and `utl` interfaces, so it is
not linked into RedstoneCompiler. Importing that dependency graph would violate
the intended small, safe Rust boundary. Executable FLUTE3 integration remains
an open, benchmark-gated acceptance item and must not be reported as live.
