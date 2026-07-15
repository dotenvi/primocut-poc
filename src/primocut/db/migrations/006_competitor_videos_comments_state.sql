-- Track the automatic top-comment collection on the video row.
--
-- The cycle collects a video's comments once it becomes a definitive outlier, and
-- must not ask again afterwards. An empty state means "never collected", so the
-- default backfills every pre-existing row correctly with no pass: none of them
-- had been collected. "done" and "disabled" are terminal, "error" is retried.
--
-- The state lives on competitor_videos rather than in its own table because it
-- models a once-per-video operation -- the same reasoning that put backfill_state
-- on the channel row.
--
-- NOTE the migration splitter naively breaks the file on the semicolon
-- character, so comments here must not contain one.
--
-- Fresh databases already have these columns from create_all, so the migration
-- runner tolerates the resulting duplicate-column error and treats it as
-- already applied.
ALTER TABLE competitor_videos ADD COLUMN comments_state VARCHAR DEFAULT '';
ALTER TABLE competitor_videos ADD COLUMN comments_error TEXT DEFAULT '';
ALTER TABLE competitor_videos ADD COLUMN comments_fetched_at DATETIME DEFAULT NULL
