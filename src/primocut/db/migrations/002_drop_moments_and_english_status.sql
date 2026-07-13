-- Pre-Phase 2 adjustments. NOTE the migration splitter naively breaks the file
-- on the semicolon character, so comments here must not contain one.
--   1. Remove the fine cut-moments detection pass (feature discontinued).
--   2. Translate the persisted TopicStatus values to English.
-- Already-processed videos keep opening. Existing topics are preserved and
-- moment data is discarded.

DROP TABLE IF EXISTS cut_moments;

UPDATE topics SET status = 'new'       WHERE status = 'novo';
UPDATE topics SET status = 'marked'    WHERE status = 'marcado';
UPDATE topics SET status = 'discarded' WHERE status = 'descartado';
