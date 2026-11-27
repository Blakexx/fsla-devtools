-- Every finding of every stored null gate (fsla-devtools' `interleaved_timing(null_gate = True)`, stored at its target's
-- label, `target`): an entry on a cell timed against a copy of itself in a second worker. `trusted` is 1 when the per-rep
-- ratios of the two copies put one inside their interquartile range, 0 when they find a worker's bias, null when a copy
-- could not run (`error`). `owner`, `label`, `arm` and `git_sha` read as in `timing_members`; a gate of the declared
-- build system the session program replaced was one registration's, kept by cell alone, and has no owner.
CREATE VIEW null_gates AS
SELECT target, key, utc, run, owner,
       CASE WHEN instr(owner, '//') > 0 THEN rtrim(substr(owner, 1, instr(owner, ':') - 1), '/') END AS label,
       CASE WHEN instr(owner, '//') > 0 THEN substr(owner, instr(owner, ':') + 1) END AS arm,
       cell, trusted, ratio_median, ratio_q1, ratio_q3, error,
       json_extract(provenance, '$.checkouts."' || owner || '".git_sha') AS git_sha
FROM (SELECT s.label AS target, s.key, s.utc, s.provenance, s.run AS run,
             json_extract(c.value, '$.owner') AS owner, json_extract(c.value, '$.cell') AS cell,
             json_extract(c.value, '$.trusted') AS trusted, json_extract(c.value, '$.ratio_median') AS ratio_median,
             json_extract(c.value, '$.ratio_q1') AS ratio_q1, json_extract(c.value, '$.ratio_q3') AS ratio_q3,
             json_extract(c.value, '$.error') AS error
      FROM samples s, json_each(s.output, '$.null') c
      WHERE s.ok AND json_type(s.output, '$.null') = 'array'
      UNION ALL
      SELECT s.label, s.key, s.utc, s.provenance, s.run, NULL, c.key,
             json_extract(c.value, '$.trusted'), json_extract(c.value, '$.ratio_median'),
             json_extract(c.value, '$.ratio_q1'), json_extract(c.value, '$.ratio_q3'), json_extract(c.value, '$.error')
      FROM samples s, json_each(s.output, '$.null') c
      WHERE s.ok AND json_type(s.output, '$.null') = 'object' AND json_type(s.output, '$.samples') = 'array');
