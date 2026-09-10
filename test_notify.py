"""notify.py 的单元测试：Telegram 推送（不打真实网络）。"""
import unittest
from unittest import mock

import notify


def _response(status_code, text="ok"):
    response = mock.Mock()
    response.status_code = status_code
    response.text = text
    return response


class SendToTelegramTest(unittest.TestCase):
    @mock.patch("notify.requests.post")
    def test_posts_html_message_to_chat(self, mock_post):
        mock_post.return_value = _response(200)

        notify.send_to_telegram("123:ABC", "-100999", "<b>hi</b>")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertEqual(kwargs["json"]["chat_id"], "-100999")
        self.assertEqual(kwargs["json"]["text"], "<b>hi</b>")
        self.assertEqual(kwargs["json"]["parse_mode"], "HTML")

    @mock.patch("notify.requests.post")
    def test_splits_long_message_into_multiple_posts(self, mock_post):
        mock_post.return_value = _response(200)
        text = "\n".join("行 {}".format(i) * 40 for i in range(60))  # 远超 4096 字符
        self.assertGreater(len(text), 4096)

        notify.send_to_telegram("123:ABC", "-100999", text)

        self.assertGreater(mock_post.call_count, 1)
        for call in mock_post.call_args_list:
            self.assertLessEqual(len(call.kwargs["json"]["text"]), 4096)

    @mock.patch("notify.requests.post")
    def test_non_200_raises_with_status_and_body(self, mock_post):
        mock_post.return_value = _response(400, '{"description":"chat not found"}')

        with self.assertRaises(RuntimeError) as ctx:
            notify.send_to_telegram("123:ABC", "-100999", "hi")
        self.assertIn("400", str(ctx.exception))
        self.assertIn("chat not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class WriteReportOutputsTest(unittest.TestCase):
    def test_writes_markdown_file_and_appends_to_step_summary(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            report_file = os.path.join(tmp, "r.md")
            step_summary = os.path.join(tmp, "summary.md")
            with open(step_summary, "w", encoding="utf-8") as f:
                f.write("已有内容\n")
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": step_summary}):
                notify.write_report_outputs("# 报告", report_file)

            with open(report_file, encoding="utf-8") as f:
                self.assertEqual(f.read(), "# 报告")
            with open(step_summary, encoding="utf-8") as f:
                self.assertEqual(f.read(), "已有内容\n# 报告\n")

    def test_skips_step_summary_when_not_on_github_actions(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            report_file = os.path.join(tmp, "r.md")
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
            with mock.patch.dict(os.environ, env, clear=True):
                notify.write_report_outputs("# 报告", report_file)
            self.assertTrue(os.path.exists(report_file))
