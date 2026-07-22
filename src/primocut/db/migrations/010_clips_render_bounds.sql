-- 010 clips.render_start_s / render_end_s: the EFFECTIVE encoded window
-- On a fresh DB create_all already made these columns, so both ADD COLUMNs raise
-- duplicate-column and are swallowed by the runner. On a legacy DB they add the
-- columns defaulting to 0 the export code then treats a 0/0 pair as "unknown" and
-- falls back to the requested bounds, so pre-existing clips still export sanely.
-- The `exports` table is a whole new table, created by create_all, not here.
-- No semicolons in comments (the migration splitter is naive).
ALTER TABLE clips ADD COLUMN render_start_s FLOAT DEFAULT 0.0;
ALTER TABLE clips ADD COLUMN render_end_s FLOAT DEFAULT 0.0;
