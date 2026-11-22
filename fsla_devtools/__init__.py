"""fsla_devtools: the development tools every FSLA repository shares, standard library only.

- `fsla_devtools.mirror`: the public mirror's export (every tracked path declared ships or private, a tripwire over
  private-shaped content), run by each repository's commit gate and its mirror workflow.
- `fsla_devtools.cells`: the central cell registry -- every input (carry, layer, QKV) with its draw, seed and regime
  proof, named once for every package -- with bases, derived cells, and the node a declaration takes a cell as.
- `fsla_devtools.build`: the declared build system -- targets declared in files, keyed, cached, held and run.
- `fsla_devtools.timing`: the timing system as targets -- interleaved clock-locked sessions, memory passes, null gates.
- `fsla_devtools.store`: the store target, a result written through fsla-results.
- `fsla_devtools.verdict`: whether a candidate's timing is a regression against a reference timed in the same sessions.
- `fsla_devtools.process`: a command stopped whole, profiler and measured process included.
- `fsla_devtools.config`: the dev config reader, and the machine sections (`host`, `clock`) every repository shares.
- `fsla_devtools.locks`: the GPU lock, the host-compute budget and file locks, the clock lock.
"""
