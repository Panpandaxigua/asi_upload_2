# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock, patch

from asi_bot import AsiBot


class MediaUploadRobustnessTests(unittest.TestCase):
    def test_file_selection_does_not_dispatch_a_second_change_event(self):
        bot = AsiBot.__new__(AsiBot)
        file_input = Mock()
        bot.tab = Mock()
        bot.tab.ele.return_value = file_input
        bot._dismiss_all_alerts = Mock()
        bot._wait_for_media_tab_ready = Mock(return_value=True)
        bot.get_visible_media_count = Mock(return_value=0)
        bot._wait_for_image_upload = Mock(return_value=1)
        bot.apply_media_criteria_tags = Mock(return_value={"changed": []})
        bot.save_current_tab = Mock(return_value=None)

        with patch("asi_bot.time.sleep"):
            result = bot._upload_images_once(["one.jpg"], [])

        self.assertTrue(result["success"])
        file_input.input.assert_called_once()
        javascript_calls = [
            call.args[0]
            for call in bot.tab.run_js.call_args_list
            if call.args and isinstance(call.args[0], str)
        ]
        self.assertFalse(
            any("dispatchEvent(new Event('change'" in script for script in javascript_calls),
            "Selecting files already fires change; dispatching it again re-enqueues the batch.",
        )


if __name__ == "__main__":
    unittest.main()
