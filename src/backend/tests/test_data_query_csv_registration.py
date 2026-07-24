import tempfile
import unittest
from pathlib import Path

from core.ontology.data_query import DataQueryEngine


class FakeOntologyEngine:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.classes = {
            "Sales": {
                "source_file": "sales.csv",
                "table_name": "sales",
                "data_source": "csv",
            }
        }

    def get_source_file(self, class_id):
        return self.classes[class_id]["source_file"]

    def get_table_name(self, class_id):
        return self.classes[class_id]["table_name"]


class DataQueryCsvRegistrationTests(unittest.TestCase):
    def test_register_csv_uses_source_file_and_normalized_table_name(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "sales.csv").write_text("amount\n100\n", encoding="utf-8")
            query_engine = DataQueryEngine(FakeOntologyEngine(data_dir))

            query_engine._register_csv("Sales")
            rows = query_engine._execute_sql('SELECT "amount" FROM "sales"')

        self.assertEqual(rows, [{"amount": 100}])


if __name__ == "__main__":
    unittest.main()