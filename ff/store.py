"""Thin SQLite layer. Every table is fully replaced on refresh; no migrations needed."""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

from .config import DATA_DIR, DB_PATH


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def save(name: str, df: pd.DataFrame) -> None:
    with connect() as con:
        df.to_sql(name, con, if_exists="replace", index=False)
        con.execute(
            "CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, updated TEXT)"
        )
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            (name, datetime.now().isoformat(timespec="seconds")),
        )


def load(name: str) -> pd.DataFrame | None:
    with connect() as con:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        if not exists:
            return None
        return pd.read_sql(f"SELECT * FROM {name}", con)


def get_meta(name: str) -> str | None:
    """Read a free-form value from the meta table (e.g. the data format version)."""
    return updated(name)


def set_meta(name: str, value: str) -> None:
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, updated TEXT)")
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (name, value))


def updated(name: str) -> str | None:
    with connect() as con:
        try:
            row = con.execute("SELECT updated FROM meta WHERE name=?", (name,)).fetchone()
        except sqlite3.OperationalError:
            return None
    return row[0] if row else None
