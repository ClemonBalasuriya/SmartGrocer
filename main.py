"""
SmartGrocer entry point.

    python main.py

First run: if no database exists yet, offers to build the ~9-month synthetic
demo dataset (see smartgrocer/data_generator.py) so every screen has
something real to show immediately. On a real shop's machine, say no and
start entering stock/sales from a clean database instead.
"""
from pathlib import Path

from smartgrocer import db


def main():
    db_exists = Path(db.DEFAULT_DB_PATH).exists()
    if not db_exists:
        answer = input(
            "No database found. Build the synthetic ~9-month demo dataset so every "
            "screen has data to show? [Y/n]: "
        ).strip().lower()
        from smartgrocer import data_generator
        if answer in ("", "y", "yes"):
            print("Building synthetic demo data (a few seconds)...")
            data_generator.build_demo_database()
        else:
            conn = db.get_conn()
            db.init_db(conn)
            conn.close()

    from smartgrocer.gui.app import run
    run()


if __name__ == "__main__":
    main()
