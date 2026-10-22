-- Test-only fake migration 0003 (good).
CREATE TABLE test_extra (id INTEGER PRIMARY KEY);
CREATE INDEX idx_test_extra ON test_extra(id);
