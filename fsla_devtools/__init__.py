"""fsla_devtools: the development tools every FSLA repository shares, standard library only.

- `fsla_devtools.mirror`: the declarations (every tracked path declared ships or private) and the private-shaped content
  the tripwire refuses, run by each repository's commit gate; `fsla_devtools.tripwire` runs the checks at commit and push.
- `fsla_devtools.cells`: the central cell registry -- every input (carry, layer, producer, QKV) in one form, with its
  draw, seed and regime proof, named once for every package by its own fields -- with bases, derived cells, and each
  cell's description and fields, which a Bazel target takes a cell as.
- `fsla_devtools.timing`: the timing system -- the program a Bazel run target starts: an interleaved, clock-locked
  session over entries of one or several commits' bundles, its null gate, the memory pass.
- `fsla_devtools.diff`: two diff sides over the same cells compared under one rule, the claim a gate can fail on.
- `fsla_devtools.results`: the results store's code -- records keyed by their semantics, every sample kept, in the data
  repository -- and its readings: the index and views, the verdict, the dashboard.
- `fsla_devtools.instrument`: an instrument run -- a tool over the files it reads, laid out as a tree, its JSON or its
  refusal the output.
- `fsla_devtools.record`: the record of a build output, the program a `results_store` run target starts: each output
  stored unless its record already holds it.
- `fsla_devtools.process`: a command stopped whole, profiler and measured process included.
- `fsla_devtools.config`: the dev config reader, and the machine sections (`host`, `clock`, `store`) every repository shares.
- `fsla_devtools.locks`: the GPU lock, the host-compute budget and file locks, the clock lock.
- `fsla_devtools.graph`: the build graph's machinery -- the front door, the environment's store builder, the bundle
  tag's export of another workspace's entries.
"""
