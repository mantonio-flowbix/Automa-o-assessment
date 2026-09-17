"""MySQL/MariaDB collector for the Zabbix backend database.

Everything here is a SELECT against information_schema or, optionally, a
bounded query against the Zabbix history tables. No writes, ever — this is
meant to run against a read-only user.
"""
from __future__ import annotations

import pymysql

HISTORY_TABLES = ["history", "history_uint", "history_str", "history_text", "history_log"]


def _connect(cfg: dict):
    return pymysql.connect(
        host=cfg["host"],
        port=cfg.get("port", 3306),
        user=cfg["user"],
        password=cfg.get("password", ""),
        database=cfg.get("database", "zabbix"),
        connect_timeout=cfg.get("connect_timeout", 10),
        cursorclass=pymysql.cursors.DictCursor,
    )


def collect(mysql_config: dict) -> dict:
    conn = _connect(mysql_config)
    result: dict = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS version")
            version = cur.fetchone()["version"]
            result["version"] = version
            is_mariadb = "mariadb" in version.lower()

            schema = mysql_config.get("database", "zabbix")

            cur.execute(
                """
                SELECT table_name, table_rows, data_length, index_length
                FROM information_schema.tables
                WHERE table_schema = %s
                ORDER BY (data_length + index_length) DESC
                """,
                (schema,),
            )
            result["tables"] = cur.fetchall()

            cur.execute(
                """
                SELECT DISTINCT table_name
                FROM information_schema.partitions
                WHERE table_schema = %s AND partition_name IS NOT NULL
                """,
                (schema,),
            )
            result["partitioned_tables"] = [r["table_name"] for r in cur.fetchall()]

            # `SHOW GLOBAL VARIABLES` instead of a catalog table: MariaDB has no
            # performance_schema.global_variables (that's MySQL-only), and
            # information_schema.global_variables was removed in MySQL 8 — SHOW
            # is the one syntax both engines have always supported.
            cur.execute(
                "SHOW GLOBAL VARIABLES WHERE Variable_name IN (%s, %s, %s)",
                ("max_connections", "event_scheduler", "innodb_io_capacity"),
            )
            result["variables"] = {r["Variable_name"]: r["Value"] for r in cur.fetchall()}

            if mysql_config.get("check_history_age", False):
                oldest = {}
                timeout_ms = mysql_config.get("history_age_query_timeout_ms", 5000)
                # Statement-timeout syntax differs between engines: MySQL's
                # MAX_EXECUTION_TIME is milliseconds and set per-session; MariaDB's
                # max_statement_time is seconds and set per-statement.
                if is_mariadb:
                    timeout_clause = f"SET STATEMENT max_statement_time={timeout_ms / 1000} FOR "
                else:
                    cur.execute(f"SET SESSION MAX_EXECUTION_TIME={int(timeout_ms)}")
                    timeout_clause = ""
                for table in HISTORY_TABLES:
                    if table not in [t["table_name"] for t in result["tables"]]:
                        continue
                    try:
                        cur.execute(f"{timeout_clause}SELECT MIN(clock) AS oldest_clock FROM {table}")
                        row = cur.fetchone()
                        oldest[table] = row["oldest_clock"] if row else None
                    except pymysql.err.OperationalError:
                        oldest[table] = None
                result["history_oldest_clock"] = oldest
    finally:
        conn.close()

    return result
