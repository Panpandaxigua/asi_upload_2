import unittest
import json
from unittest.mock import Mock

from tools import fill_categories_modal


class CategoryWorkflowTests(unittest.TestCase):
    def test_slash_preserved_and_apply_after_verification(self):
        tab = Mock()
        tab.run_js.return_value = 'ok'
        self.assertTrue(fill_categories_modal(tab, 'TRAVEL MUGS/CUPS; Bottles-General'))
        calls = tab.run_js.call_args_list
        self.assertEqual([c.args[1] for c in calls],
                         ['open', 'select', 'verify', 'apply', 'closed'])
        self.assertIsInstance(calls[1].args[2], str)
        self.assertEqual(json.loads(calls[1].args[2]), ['TRAVEL MUGS/CUPS', 'Bottles-General'])
        for call in calls:
            self.assertTrue(all(isinstance(arg, str) for arg in call.args))

    def test_disabled_category_does_not_apply(self):
        tab = Mock()
        tab.run_js.side_effect = ['ok', '分类不可选']
        with self.assertRaisesRegex(RuntimeError, '分类不可选'):
            fill_categories_modal(tab, 'Cups-Paper')
        self.assertEqual(tab.run_js.call_count, 2)

    def test_missing_category_times_out_without_apply(self):
        tab = Mock()
        tab.run_js.side_effect = ['ok', 'wait']
        with self.assertRaisesRegex(RuntimeError, 'select'):
            fill_categories_modal(tab, 'Unknown Category', timeout=0)
        self.assertEqual(tab.run_js.call_count, 2)

    def test_unchecked_category_does_not_apply(self):
        tab = Mock()
        tab.run_js.side_effect = ['ok', 'ok', 'wait']
        with self.assertRaisesRegex(RuntimeError, 'verify'):
            fill_categories_modal(tab, 'Cups-Paper', timeout=0)


if __name__ == '__main__':
    unittest.main()
