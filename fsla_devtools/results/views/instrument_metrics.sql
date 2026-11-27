-- EVERY NUMBER AN INSTRUMENT RECORDED, one row each: the long form of the instrument outputs (each at its store
-- target's label, `instrument`), so a reading across runs -- a kernel's HMMA count before and after -- is a self-join
-- on (instrument, cell, metric) and never a parser per tool. `cell` is NULL for a whole-binary instrument (`sass`),
-- whose output is keyed by the binary and not by a cell. `metric` is dotted: `sass.<cubin>.<function>.<count>`.
-- Booleans are 0/1. A branch per output shape.
CREATE VIEW instrument_metrics AS
WITH inst AS (
  SELECT s.label AS target, s.label AS instrument, s.key, s.utc,
         json_extract(s.provenance, '$.run') AS run, s.output
  FROM samples s WHERE s.ok AND json_type(s.semantics, '$.inputs') = 'object' AND json_valid(s.output)
)
SELECT inst.target, inst.instrument, inst.key, inst.utc, inst.run, NULL AS cell,
       'sass.' || cub.key || '.' || fn.key || '.' || f.key AS metric, f.value AS value
  FROM inst, json_each(inst.output) member, json_each(member.value) cub, json_each(cub.value, '$.functions') fn,
       json_each(fn.value) f
  WHERE json_type(cub.value, '$.functions') = 'object';
