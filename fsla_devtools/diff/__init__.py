"""THE DIFF: two sides over the same cells, one comparison rule, and a claim a gate can fail on.

`bazel/measure.bzl`'s `diff_side` is a side: an executor (`module:function`, taking a cell's description and returning
`{name: tensor}`) over cells in a bundle; another commit's side is the same description over a bundle the `bundle` tag
built (`exported_entry`). `diff_verdict` compares two sides under one named rule (`strategies.py`: `bit-identical`,
`per-slot`, `support-equal`): its tool, `verdict.py`, produces each side on its own bundle's interpreter (`side.py`) and
compares them; `diff_test` is its gate, and `results_store` records it.

    diff_side(name = "side_oracle", bundle = ":side_bundle", executor = "tests.oracle.sides:oracle", cells = [...])
    diff_verdict(name = "diff_oracle", left = "@candidate//:side_oracle", right = "@reference//:side_oracle",
                 strategy = "bit-identical", minimum = N)
    diff_test(name = "diff_oracle_test", verdict = ":diff_oracle")

WHAT IT IS FOR. "The same function in two commits" and "the kernel against its fp64 reference" are the same question
asked twice: run both, compare per cell under a stated rule, and say whether the difference is the one that was
expected. THE RAW NEVER LEAVES THE RUN: each side's tensors go to the verdict's scratch, and what the verdict outputs,
and a store target files, is the DIFF -- per cell and quantity the worst slot and where it is, what bound it, how many
slots were compared and how many failed.
"""
