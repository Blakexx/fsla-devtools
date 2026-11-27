-- Every cell of every stored per-cell instrument run (the declared build system's instrument targets, stored at
-- its target's label with each cell's status in the sample's summary): one row per (sample, cell), whether the instrument
-- ran on the cell (`ok`) or could not (`failed`, with the tool's exit and error). The cells that ran hold their results
-- in the sample's output file.
CREATE VIEW instrument_cells AS
SELECT s.label AS target, s.key, s.utc,
       json_extract(s.extra, '$.run') AS run,
       s.label AS instrument,
       c.key AS cell,
       json_extract(c.value, '$.status') AS status,
       json_extract(c.value, '$.exit') AS exit,
       json_extract(c.value, '$.error') AS error
FROM samples s, json_each(s.extra, '$.summary.cells') c
WHERE s.ok AND json_type(s.extra, '$.summary.cells') = 'object';
