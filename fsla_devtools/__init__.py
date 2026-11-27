"""fsla_devtools: the development tools every FSLA repository shares, standard library only.

- `fsla_devtools.mirror`: the declarations (every tracked path declared ships or private) and the private-shaped content
  the tripwire refuses, run by each repository's commit gate; `fsla_devtools.tripwire` runs the checks at commit and push.
- `fsla_devtools.cells`: the central cell registry -- every input (carry, layer, QKV) with its draw, seed and regime
  proof, named once for every package -- with bases, derived cells, and the node a declaration takes a cell as.
- `fsla_devtools.build`: the declared build system -- targets declared in files, keyed, cached, held and run.
- `fsla_devtools.timing`: the timing system as targets -- interleaved clock-locked sessions, memory passes, null gates.
- `fsla_devtools.results`: the results store's code -- records keyed by their semantics, every sample kept, in the data
  repository -- and its readings: the index and views, the verdict, the instruments' deltas, the dashboard.
- `fsla_devtools.store`: the store target, a result written through `fsla_devtools.results`.
- `fsla_devtools.process`: a command stopped whole, profiler and measured process included.
- `fsla_devtools.config`: the dev config reader, and the machine sections (`host`, `clock`, `store`) every repository shares.
- `fsla_devtools.locks`: the GPU lock, the host-compute budget and file locks, the clock lock.
"""
