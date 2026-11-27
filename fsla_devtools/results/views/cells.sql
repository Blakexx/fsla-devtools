-- Every stored grid cell, fleet or local (records whose semantics name a config and a cell): its code (the box image,
-- or the local checkouts), whether the sample succeeded, and its row.
CREATE VIEW cells AS
SELECT s.label AS target, s.key, s.utc, s.ok,
       json_extract(s.semantics, '$.bench') AS bench,
       json_extract(s.semantics, '$.config') AS config,
       json_extract(s.semantics, '$.cell') AS cell,
       json_extract(s.semantics, '$.image') AS image,
       json_extract(s.semantics, '$.local') AS local,
       s.output, s.error
FROM samples s
WHERE json_extract(s.semantics, '$.config') IS NOT NULL AND json_extract(s.semantics, '$.cell') IS NOT NULL;
