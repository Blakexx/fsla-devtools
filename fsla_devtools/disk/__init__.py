"""THE MACHINE'S DISK: what keeps a development machine's disk from filling, every FSLA repository's, run through its
front door (`fsla_devtools.graph.front_door`) with no Bazel started.

- `worktrees`: the cap on live linked worktrees across the machine's repositories (`host.worktree_cap`), the census
  that names the clean, merged ones, and the removal that takes a worktree's Bazel output base with it
  (`bazel --worktree add|remove|list`).
- `floor`: the free-space floor (`host.free_floor_gb`) a build, test or run is refused under, before Bazel starts.
- `downloads`: the trim of Bazel's repository cache, the downloads unused for `host.downloads_max_age_days`, at most
  once a day.

Standard library only: the front door runs under the machine's `python3` before any environment exists. Docs:
README.md#the-machines-disk.
"""
