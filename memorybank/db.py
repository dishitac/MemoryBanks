"""MongoDB Atlas client, shared by the whole project (and by every teammate's scripts).

Connection details come only from MONGODB_URI in the local .env file. TLS uses certifi's CA
bundle, the same way the original test_mongodb.py did, so everyone connects identically.

Check your connection with:  python -m memorybank.db
"""

from functools import lru_cache

import certifi
from pymongo import MongoClient
from pymongo.database import Database

from memorybank.config import get_settings


@lru_cache
def get_client() -> MongoClient:
    settings = get_settings()
    return MongoClient(
        settings.mongodb_uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=8000,
    )


def get_db() -> Database:
    return get_client()[get_settings().mongodb_db]


def check_connection() -> str:
    """Ping the cluster. Never prints the URI or credentials."""
    client = get_client()
    client.admin.command("ping")
    return f"Connected successfully (database: {get_settings().mongodb_db})"


if __name__ == "__main__":
    print(check_connection())
