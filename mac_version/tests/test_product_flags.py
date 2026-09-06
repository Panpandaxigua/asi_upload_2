# -*- coding: utf-8 -*-
import unittest

from product_flags import (
    PRODUCT_FLAG_SCHEMA,
    default_product_flags,
    normalize_product_flags,
)


class ProductFlagsTests(unittest.TestCase):
    def test_defaults_cover_exactly_the_seven_software_switches(self):
        self.assertEqual(
            list(PRODUCT_FLAG_SCHEMA),
            [
                "distributor_only_view",
                "new_product",
                "seo_product",
                "close_out",
                "product_confirmed",
                "prop65_no_chemicals",
                "unimprinted",
            ],
        )
        self.assertEqual(
            default_product_flags(),
            {
                "distributor_only_view": False,
                "new_product": True,
                "seo_product": False,
                "close_out": False,
                "product_confirmed": True,
                "prop65_no_chemicals": True,
                "unimprinted": True,
            },
        )

    def test_normalization_keeps_only_boolean_known_values(self):
        normalized = normalize_product_flags(
            {
                "distributor_only_view": True,
                "new_product": False,
                "seo_product": "yes",
                "unknown": True,
            }
        )

        self.assertTrue(normalized["distributor_only_view"])
        self.assertFalse(normalized["new_product"])
        self.assertFalse(normalized["seo_product"])
        self.assertNotIn("unknown", normalized)


if __name__ == "__main__":
    unittest.main()
