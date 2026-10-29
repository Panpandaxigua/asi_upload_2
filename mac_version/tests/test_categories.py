import unittest
from unittest.mock import Mock

from tools import fill_categories_modal


class CategoryWorkflowTests(unittest.TestCase):
    def test_slash_preserved_and_apply_after_verification(self):
        tab = Mock()
        tab.run_js.return_value = 'ok'
        self.assertTrue(fill_categories_modal(tab, 'TRAVEL MUGS/CUPS; Bottles-General'))
        calls = tab.run_js.call_args_list
        self.assertEqual([c.args[1] for c in calls],
                         ['open', 'select', 'clear_search', 'select',
                          'clear_search', 'apply', 'closed'])
        self.assertEqual(calls[1].args[2], 'TRAVEL MUGS/CUPS')
        self.assertEqual(calls[3].args[2], 'Bottles-General')
        self.assertIn('categorySearchTerm', calls[1].args[0])
        self.assertIn('search categories', calls[1].args[0])
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
        tab.run_js.side_effect = ['ok', 'wait', '{"search":"Unknown Category"}']
        with self.assertRaisesRegex(RuntimeError, "分类 'Unknown Category' 搜索无结果"):
            fill_categories_modal(tab, 'Unknown Category', timeout=0)
        self.assertEqual(tab.run_js.call_count, 3)

    def test_apply_timeout_reports_modal_state(self):
        tab = Mock()
        tab.run_js.side_effect = ['ok', 'ok', 'ok', 'wait', '{"selectionCount":"1"}']
        with self.assertRaisesRegex(RuntimeError, r'操作超时 \(apply\)'):
            fill_categories_modal(tab, 'Cups-Paper', timeout=0)


if __name__ == '__main__':
    unittest.main()
