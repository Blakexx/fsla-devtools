-- EVERY NUMBER AN INSTRUMENT RECORDED, one row each: the long form of the instrument outputs (`fsla/<instrument>`), so
-- a reading across runs -- a kernel's HMMA count before and after -- is a self-join on (instrument, cell, metric) and
-- never a parser per tool. `cell` is NULL for a whole-binary instrument (`sass`), whose output is keyed by the binary
-- and not by a cell. `metric` is dotted: `sass.<cubin>.<function>.<count>`. Booleans are 0/1. A branch per instrument a
-- checkout declares; the records of an instrument that no longer exists stay in `samples`, read by a query of their own.
CREATE VIEW instrument_metrics AS
WITH inst AS (
  SELECT s.location, substr(s.location, instr(s.location, '/') + 1) AS instrument, s.key, s.n, s.utc,
         json_extract(s.provenance, '$.run') AS run, s.output
  FROM samples s WHERE s.ok AND s.location LIKE 'fsla/%' AND json_valid(s.output)
)
SELECT inst.location, inst.instrument, inst.key, inst.n, inst.utc, inst.run, NULL AS cell,
       'sass.' || cub.key || '.' || fn.key || '.' || f.key AS metric, f.value AS value
  FROM inst, json_each(inst.output) member, json_each(member.value) cub, json_each(cub.value, '$.functions') fn,
       json_each(fn.value) f
  WHERE instrument = 'sass';
