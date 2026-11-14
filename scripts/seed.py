"""Insert demo identities and missing search-demo memories after running setup_db."""

from memorybank.db import get_db
from memorybank.seed import seed_database


def main():
    created = seed_database(get_db())
    print("Missing demo policies created." if created else "Existing policy history preserved.")
    print("Demo identities ready. Embeddings and the gateway are the next checkpoint.")


if __name__ == "__main__":
    main()
