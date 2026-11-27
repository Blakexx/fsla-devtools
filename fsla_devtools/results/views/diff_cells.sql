-- EVERY CELL OF EVERY STORED DIFF (fsla-devtools' diff targets: a checkout's own kernel-vs-oracle, the suite's
-- cross-checkout diffs, each at its target's label, `target`): one row per (sample, cell, quantity).
-- `held` is the diff's claim as a whole (its `expect`, `same` or `different`, kept over every cell and the count);
-- `cell_status` is `ok`, `unusable` (one side could not produce the cell, or the two drew it with different code) or
-- `refused` (neither side produced it), and `why` says which; `same` is the quantity's own verdict under the strategy,
-- with why it differs (`quantity_why`) and the worst slot (`ratio` of its error to its allowance, `at`, `bound_by`)
-- where it failed. A diff stores verdicts and never the tensors.
CREATE VIEW diff_cells AS
SELECT s.label AS target, s.key, s.utc,
       json_extract(s.provenance, '$.run') AS run,
       json_extract(s.output, '$.strategy') AS strategy,
       json_extract(s.output, '$.expect') AS expect,
       json_extract(s.output, '$.held') AS held,
       json_extract(s.output, '$.compared') AS compared,
       json_extract(s.output, '$.quantities') AS quantities,
       json_extract(s.output, '$.minimum') AS minimum,
       c.key AS cell,
       json_extract(c.value, '$.status') AS cell_status,
       coalesce(json_extract(c.value, '$.why'), json_extract(c.value, '$.left'), json_extract(c.value, '$.right')) AS why,
       q.key AS quantity,
       json_extract(q.value, '$.same') AS same,
       json_extract(q.value, '$.why') AS quantity_why,
       json_extract(q.value, '$.failed') AS failed,
       json_extract(q.value, '$.slots') AS slots,
       json_extract(q.value, '$.ratio') AS ratio,
       json_extract(q.value, '$.at') AS at,
       json_extract(q.value, '$.bound_by') AS bound_by
FROM samples s, json_each(s.output, '$.cells') c
     LEFT JOIN json_each(c.value, '$.quantities') q
WHERE s.ok AND json_type(s.output, '$.cells') = 'object';
