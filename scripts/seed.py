"""Insert demo identities and the initial policy after running setup_db."""

from memorybank.db import get_db
from memorybank.seed import seed_database


def main():
    created = seed_database(get_db())
    print("Initial policy created." if created else "Existing policy history preserved.")
    print("Demo identities ready. Embeddings and the gateway are the next checkpoint.")


if __name__ == "__main__":
    main()
