import unittest

from core.ontology.data_query import DataQueryEngine


class DataQueryMetricErrorTests(unittest.TestCase):
    def test_structured_input_error_contains_metric_id(self):
        query_engine = DataQueryEngine.__new__(DataQueryEngine)

        with self.assertRaises(ValueError) as context:
            query_engine._definition_input_expr(
                {"class_id": "Sales", "field": "amount", "aggregation": "SUM"},
                {},
                "actual_sales",
            )

        self.assertIn("Metric actual_sales", str(context.exception))
        self.assertIn("class_id=Sales", str(context.exception))

    def test_invalid_v2_output_error_contains_metric_id(self):
        query_engine = DataQueryEngine.__new__(DataQueryEngine)

        with self.assertRaises(ValueError) as context:
            query_engine._definition_output_expr({}, {}, "sales_progress")

        self.assertIn("Metric sales_progress", str(context.exception))


if __name__ == "__main__":
    unittest.main()
