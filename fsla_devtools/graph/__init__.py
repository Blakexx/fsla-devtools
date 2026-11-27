"""THE BUILD GRAPH'S SHARED MACHINERY, every FSLA repository's: the front door each repository's `bazel/bazel` stub runs
(`front_door`), the environment's store builder the `fsla_devtools` Bazel module's environment extension runs
(`make_env`), the bundle tag's export of another workspace's timing entries and diff sides (`export`), and the test
tiers' sharding plugin (`pytest_shards`). Standard library only: the front door runs under
the machine's `python3` before any environment exists. Docs: README.md#the-build-graph.
"""
