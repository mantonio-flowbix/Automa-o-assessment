"""Mock-based tests for flowbix_assess.collectors.mysql_collector.

No real MySQL/MariaDB needed — `pymysql.connect` (via the module's
`_connect`) is replaced with a fake cursor that records every `execute()`
call and returns canned rows by matching a substring in the SQL. This is
exactly the seam where a real MariaDB server broke in production
(`performance_schema.global_variables` doesn't exist there) — these tests
pin down that fix and the statement-timeout syntax that differs between
MySQL and MariaDB.
"""
from __future__ import annotations

import pymysql
import pytest

from flowbix_assess.collectors import mysql_collector

TABLES = [
    {"table_name": "history", "table_rows": 1000, "data_length": 2048, "index_length": 512},
    {"table_name": "hosts", "table_rows": 10, "data_length": 1024, "index_length": 256},
]
VARIABLES_ROWS = [
    {"Variable_name": "max_connections", "Value": "150"},
    {"Variable_name": "event_scheduler", "Value": "ON"},
    {"Variable_name": "innodb_io_capacity", "Value": "2000"},
]


class FakeCursor:
    def __init__(self, version: str):
        self.version = version
        self.queries: list[str] = []
        self._last: list = []

    def _respond(self, sql: str):
        if "SELECT VERSION()" in sql:
            return [{"version": self.version}]
        if "information_schema.tables" in sql:
            return TABLES
        if "information_schema.partitions" in sql:
            return []
        if "SHOW GLOBAL VARIABLES" in sql:
            return VARIABLES_ROWS
        if "SELECT MIN(clock)" in sql:
            return [{"oldest_clock": 1700000000}]
        return []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        self._last = self._respond(sql)

    def fetchall(self):
        return self._last

    def fetchone(self):
        return self._last[0] if self._last else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _patch_connect(monkeypatch, version: str) -> FakeCursor:
    cursor = FakeCursor(version)
    monkeypatch.setattr(mysql_collector, "_connect", lambda cfg: FakeConnection(cursor))
    return cursor


def test_uses_show_global_variables_not_performance_schema(monkeypatch):
    cursor = _patch_connect(monkeypatch, "8.0.35")
    result = mysql_collector.collect({"host": "db", "user": "u", "password": "p"})

    all_sql = " ".join(cursor.queries)
    assert "SHOW GLOBAL VARIABLES" in all_sql
    assert "performance_schema.global_variables" not in all_sql
    assert result["variables"] == {"max_connections": "150", "event_scheduler": "ON", "innodb_io_capacity": "2000"}


def test_mariadb_uses_per_statement_timeout(monkeypatch):
    cursor = _patch_connect(monkeypatch, "10.11.14-MariaDB-0ubuntu0.24.04.1-log")
    mysql_collector.collect({
        "host": "db", "user": "u", "password": "p",
        "check_history_age": True, "history_age_query_timeout_ms": 5000,
    })

    all_sql = " ".join(cursor.queries)
    assert "max_statement_time=5.0" in all_sql
    assert "SET SESSION MAX_EXECUTION_TIME" not in all_sql


def test_mysql_uses_session_execution_timeout(monkeypatch):
    cursor = _patch_connect(monkeypatch, "8.0.35")
    mysql_collector.collect({
        "host": "db", "user": "u", "password": "p",
        "check_history_age": True, "history_age_query_timeout_ms": 5000,
    })

    all_sql = " ".join(cursor.queries)
    assert "SET SESSION MAX_EXECUTION_TIME=5000" in all_sql
    assert "max_statement_time" not in all_sql


def test_history_age_only_queries_tables_that_exist(monkeypatch):
    cursor = _patch_connect(monkeypatch, "8.0.35")
    result = mysql_collector.collect({
        "host": "db", "user": "u", "password": "p",
        "check_history_age": True, "history_age_query_timeout_ms": 5000,
    })

    # "history" is in TABLES, the other HISTORY_TABLES entries are not.
    assert result["history_oldest_clock"] == {"history": 1700000000}


def test_connection_error_propagates(monkeypatch):
    def _raise(cfg):
        raise pymysql.err.OperationalError(1045, "Access denied")

    monkeypatch.setattr(mysql_collector, "_connect", _raise)
    with pytest.raises(pymysql.err.OperationalError):
        mysql_collector.collect({"host": "db", "user": "u", "password": "wrong"})
