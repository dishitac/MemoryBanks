"""Create the governed-memory collections and constraints. Does not delete data."""

from memorybank.db import get_db
from memorybank.schema import setup_database


def main():
    for name in setup_database(get_db()):
        print(f"Ready: {name}")


if __name__ == "__main__":
    main()
