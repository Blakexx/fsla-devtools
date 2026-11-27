#!/bin/bash
# THE BUILD'S STAMP (.bazelrc's --workspace_status_command): the commit, branch, checkout name and the digest of the
# uncommitted tracked diff (`git diff HEAD`), as the stable keys a bundle manifest reads (fsla-devtools'
# bazel/measure.bzl). A git that fails fails the build: a number never names code it cannot name.
set -euo pipefail
top="$(git rev-parse --show-toplevel)"
diff=""
if ! git -C "$top" diff --quiet HEAD -- .; then
  diff="$(git -C "$top" diff HEAD -- . | sha256sum | cut -d' ' -f1)"
fi
echo "STABLE_GIT_COMMIT $(git -C "$top" rev-parse HEAD)"
echo "STABLE_GIT_BRANCH $(git -C "$top" symbolic-ref --short -q HEAD || true)"
echo "STABLE_GIT_DIFF_SHA256 $diff"
echo "STABLE_CHECKOUT $(basename "$top")"
