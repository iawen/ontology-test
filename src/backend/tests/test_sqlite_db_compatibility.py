import unittest

from core.db import db_provider, sqlite_db


class SqliteDbCompatibilityTests(unittest.TestCase):
    def test_legacy_sqlite_module_delegates_to_canonical_provider(self):
        self.assertIs(sqlite_db.get_db, db_provider.get_db)
        self.assertIs(sqlite_db.init_db, db_provider.init_db)
        self.assertIs(sqlite_db.get_db_type, db_provider.get_db_type)


if __name__ == "__main__":
    unittest.main()
