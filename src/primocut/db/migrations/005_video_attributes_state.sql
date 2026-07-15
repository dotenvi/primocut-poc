-- Track the opt-in AI enrichment of a video's packaging on the attributes row.
-- The LLM call runs asynchronously on the queue, so the UI needs somewhere to
-- poll: state moves running -> done, or -> error carrying the message that a
-- failed video shows instead of a blank panel.
--
-- The "done" default is what keeps the pre-existing deterministic (syntactic)
-- rows correct without a backfill pass: they were never asynchronous.
--
-- NOTE the migration splitter naively breaks the file on the semicolon
-- character, so comments here must not contain one.
--
-- Fresh databases already have these columns from create_all, so the migration
-- runner tolerates the resulting duplicate-column error and treats it as
-- already applied.
ALTER TABLE video_attributes ADD COLUMN state VARCHAR DEFAULT 'done';
ALTER TABLE video_attributes ADD COLUMN error TEXT DEFAULT ''
