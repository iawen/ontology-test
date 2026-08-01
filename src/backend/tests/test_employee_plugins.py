import unittest

from agents.ontology_chatbi.plugins import NullEmployeePlugin, load_employee_plugin


class EmployeePluginTests(unittest.IsolatedAsyncioTestCase):
    def test_pfizer_plugin_is_loaded_by_agent_id(self):
        plugin = load_employee_plugin("pfizer")

        self.assertEqual(plugin.__class__.__module__, "agents.ontology_chatbi.plugins.pfizer_employee")

    def test_unknown_agent_uses_inert_plugin(self):
        filters = [{"field": "rm_employee_name", "operator": "=", "value": "卞哲"}]
        plugin = load_employee_plugin("unknown-agent")

        self.assertIsInstance(plugin, NullEmployeePlugin)
        self.assertEqual(plugin.merge_locked_filters(filters, []), filters)

    def test_pfizer_plugin_preserves_locked_employee_role(self):
        locked = [{"field": "bd_employee_name", "operator": "=", "value": "卞哲"}]
        filters = [*locked, {"field": "rm_employee_name", "operator": "=", "value": "卞哲"}]

        actual = load_employee_plugin("pfizer").merge_locked_filters(filters, locked)

        self.assertEqual(actual, locked)

    async def test_empty_plugin_does_not_rewrite_person_filter(self):
        plugin = NullEmployeePlugin()
        source = {"field": "人员", "operator": "=", "value": "张三"}

        self.assertFalse(plugin.is_employee_filter(source))
        self.assertEqual(await plugin.align_filter(source, [], None), source)


if __name__ == "__main__":
    unittest.main()