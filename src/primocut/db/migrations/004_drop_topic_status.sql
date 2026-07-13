-- Drop the topics.status column. It only ever fed the retired "export cuts"
-- feature (topics flagged as marked were exported as a JSON cut list), and
-- nothing reads or writes the column anymore.
--
-- NOTE the migration splitter naively breaks the file on the semicolon
-- character, so comments here must not contain one.
--
-- Fresh databases never create this column (it is gone from the ORM), so the
-- migration runner tolerates the resulting no-such-column error as a no-op.
ALTER TABLE topics DROP COLUMN status
