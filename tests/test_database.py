import unittest
from unittest.mock import Mock

from database import DatabaseConnection, lock_row


class PostgreSQLAdapterTests(unittest.TestCase):
    def setUp(self):
        self.connection = Mock()
        self.db = DatabaseConnection(self.connection, postgres=True)

    def test_execute_translates_qmark_parameters_for_psycopg(self):
        self.db.execute("SELECT id FROM events WHERE name = ? AND venue = ?", ("show", "hall"))

        self.connection.execute.assert_called_once_with(
            "SELECT id FROM events WHERE name = %s AND venue = %s",
            ("show", "hall"),
        )

    def test_executemany_uses_a_cursor_and_translates_parameters(self):
        cursor = self.connection.cursor.return_value
        rows = [(1,), (2,)]

        result = self.db.executemany("INSERT INTO events (id) VALUES (?)", rows)

        cursor.executemany.assert_called_once_with(
            "INSERT INTO events (id) VALUES (%s)",
            rows,
        )
        self.assertIs(result, cursor.executemany.return_value)

    def test_lock_row_uses_postgresql_for_update(self):
        lock_row(self.db, "SELECT id FROM ticket_types WHERE id = ?", (4,))

        self.connection.execute.assert_called_once_with(
            "SELECT id FROM ticket_types WHERE id = %s FOR UPDATE",
            (4,),
        )


if __name__ == "__main__":
    unittest.main()
