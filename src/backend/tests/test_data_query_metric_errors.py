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
    
    def test_conditional_distinct_count_ratio_compiles_with_decimal_division(self):
        query_engine = DataQueryEngine.__new__(DataQueryEngine)
        query_engine._col_ref = lambda alias, field: f'{alias}."{field}"'
        query_engine._ensure_query_field = lambda class_id, field, context: field
        query_engine._build_filter_clause = lambda class_id, filter_item, alias: (
            f'{alias}."{filter_item["field"]}" {filter_item["operator"]} '
            f"({', '.join(repr(value) for value in filter_item['value'])})"
        )
        metric = {
            "id": "listing_account_ratio",
            "definition": {
                "version": 1,
                "expression_operator": "DIVIDE",
                "inputs": [
                    {
                        "class_id": "listing",
                        "field": "account_cd",
                        "aggregation": "COUNT_DISTINCT",
                        "filters": [{
                            "field": "listing_status_subcategory",
                            "operator": "IN",
                            "value": ["临时采购", "二次准入", "正式准入"],
                        }],
                    },
                    {
                        "class_id": "listing",
                        "field": "account_cd",
                        "aggregation": "COUNT_DISTINCT",
                        "filters": [],
                    },
                ],
            },
        }

        expression = query_engine._definition_metric_expr(metric, {"listing": "t0"})

        self.assertEqual(
            expression,
            '((1.0 * COUNT(DISTINCT CASE WHEN t0."listing_status_subcategory" IN '
            "('临时采购', '二次准入', '正式准入') THEN t0.\"account_cd\" END)) / "
            'NULLIF(COUNT(DISTINCT t0."account_cd"), 0))',
        )


if __name__ == "__main__":
    unittest.main()
