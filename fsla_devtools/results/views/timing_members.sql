-- Every member of every stored timing session (fsla-devtools' `interleaved_timing`, stored at its session's label,
-- `target`): one row per (session sample, member). `owner` is the entry that made the member: a Bazel label
-- (`@candidate//:entmax_solve_<cell>`, `//measure:flash_<cell>`), whose `label` is its repository or package
-- (`@candidate`, `//measure`) and `arm` its target's name; or, in a sample of the declared build system the session
-- program replaced, a registration (`tip/carry_forward`), whose `label` is its scope (`tip`) and `arm` its last segment.
-- `status` is `ok` or `failed` (an entry that could not run, with its `error`); `git_sha`, `diff_sha256` and `checkout`
-- are the owner's checkout (the sample's provenance); `device` and `torch` are what the entry built, and `level` is what
-- it prices -- `kernel`, `op` or `layer` (Blake, 2026-09-15) -- so a reading pairs members of ONE level: a library's
-- layer against another library's layer, never a layer against a kernel. The session's samples are `timing_samples`; no
-- ratio is stored, and which label is the reference is the reader's choice
-- (`python -m fsla_devtools.results verdict --reference LABEL`).
CREATE VIEW timing_members AS
SELECT target, key, utc, run, session, instrument, rounds, reps, clock_before, clock_after, member, owner,
       CASE WHEN instr(owner, '//') > 0 THEN rtrim(substr(owner, 1, instr(owner, ':') - 1), '/')
            ELSE rtrim(prefix, '/') END AS label,
       CASE WHEN instr(owner, '//') > 0 THEN substr(owner, instr(owner, ':') + 1)
            ELSE substr(owner, length(prefix) + 1) END AS arm,
       cell, level, status, error, device, torch,
       json_extract(provenance, '$.checkouts."' || owner || '".git_sha') AS git_sha,
       json_extract(provenance, '$.checkouts."' || owner || '".diff_sha256') AS diff_sha256,
       json_extract(provenance, '$.checkouts."' || owner || '".checkout') AS checkout
FROM (SELECT s.label AS target, s.key, s.utc, s.provenance,
             s.run AS run,
             json_extract(s.output, '$.session') AS session,
             json_extract(s.output, '$.instrument') AS instrument,
             json_extract(s.output, '$.rounds') AS rounds,
             json_extract(s.output, '$.reps') AS reps,
             json_extract(s.output, '$.clock.before') AS clock_before,
             json_extract(s.output, '$.clock.after') AS clock_after,
             json_extract(m.value, '$.id') AS member,
             json_extract(m.value, '$.owner') AS owner,
             rtrim(json_extract(m.value, '$.owner'), replace(json_extract(m.value, '$.owner'), '/', '')) AS prefix,
             json_extract(m.value, '$.cell') AS cell,
             json_extract(m.value, '$.status') AS status,
             json_extract(m.value, '$.error') AS error,
             json_extract(m.value, '$.built.level') AS level,
             json_extract(m.value, '$.built.device') AS device,
             json_extract(m.value, '$.built.torch') AS torch
      FROM samples s, json_each(s.output, '$.members') m
      WHERE s.ok AND json_type(s.output, '$.members') = 'array' AND json_type(s.output, '$.samples') = 'array');
