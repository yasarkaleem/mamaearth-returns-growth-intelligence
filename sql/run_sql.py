"""Portable SQLite runner: schema -> seed -> reports, with fresh tables each run."""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def statements(script):
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""
    if buffer.strip() and any(not line.lstrip().startswith("--")
                              for line in buffer.splitlines() if line.strip()):
        raise ValueError("Incomplete SQL statement")


def run(database=None, verbose=True):
    output_dir = ROOT / "sql" / "generated"
    output_dir.mkdir(exist_ok=True)
    database = database or output_dir / "mamaearth.db"
    results = []
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript((ROOT / "sql/schema.sql").read_text())
        connection.executescript((ROOT / "sql/seed_data.sql").read_text())
        for table, expected in (("customers", 45), ("products", 16), ("orders", 180)):
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == expected, (table, count)
            if verbose:
                print(f"{table}: {count}")
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        for statement in statements((ROOT / "sql/reports.sql").read_text()):
            cursor = connection.execute(statement)
            if cursor.description:
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                results.append({"columns": columns, "rows": rows})
                if verbose:
                    print("\n" + " | ".join(columns))
                    for row in rows:
                        print(" | ".join(str(value) for value in row))
    (output_dir / "reports.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


if __name__ == "__main__":
    run()
