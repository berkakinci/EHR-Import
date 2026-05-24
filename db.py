#!/usr/bin/env python3
"""Database management — thin entry point."""
import sys

from ehr_import import config
from ehr_import.store import Database, print_data_completeness

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print_data_completeness()
    else:
        db = Database()
        with db:
            db.init_tables()
        print(f"Database initialized at {config.db_path}")
        print("\nTip: run 'python db.py status' to check data completeness")
