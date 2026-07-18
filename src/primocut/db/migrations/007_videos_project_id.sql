-- 007 videos.project_id: lives are contained in a project (niche)
-- On a fresh DB create_all already made videos with a NOT NULL project_id, so
-- this ADD COLUMN raises duplicate-column and is swallowed by the runner. On a
-- legacy DB it adds the column as nullable (SQLite cannot add a NOT NULL column
-- to a populated table) and the Python step backfill_video_projects() fills it
-- right after, assigning orphan lives to the oldest project and logging it.
-- No semicolons in comments (the migration splitter is naive).
ALTER TABLE videos ADD COLUMN project_id INTEGER REFERENCES projects(id);
