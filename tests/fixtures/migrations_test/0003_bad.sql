-- Test-only fake migration 0003 (bad: the second statement fails after the first succeeded).
CREATE TABLE test_partial (id INTEGER PRIMARY KEY);
CREATE TABLE test_note (id INTEGER PRIMARY KEY);
