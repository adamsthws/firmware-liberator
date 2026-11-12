"""The email question as the tool exits (tools/autoflash/signup.py)."""
import unittest
from unittest import mock

from tools.autoflash import interrupt, output, prompts, readout, runs, signup

URL = "http://127.0.0.1:9/readout"


class AskOnExitTests(unittest.TestCase):
    def setUp(self):
        self.settings = {}
        for patch in (mock.patch.object(signup, "SITTING", signup.Sitting()),
                      mock.patch.object(prompts, "UNATTENDED", False),
                      mock.patch.object(readout, "upload_url", return_value=URL),
                      mock.patch.object(interrupt, "window_closed", return_value=False),
                      mock.patch.object(runs, "load_settings", side_effect=lambda: dict(self.settings)),
                      mock.patch.object(runs, "save_settings", side_effect=self.settings.update),
                      mock.patch.object(output, "tell"), mock.patch.object(output, "log_only")):
            patch.start()
            self.addCleanup(patch.stop)
        self.notify = mock.patch.object(readout, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def answered(self, *answers):
        """ask_on_exit with each question answered in turn, as typed: what
        the screen was told, and the options offered."""
        typed = iter(answers)

        def answer(question, default, private=None, hint=True):
            given = next(typed)
            if isinstance(given, BaseException):
                raise given
            if given.lower() in prompts.QUIT_WORDS:
                raise prompts.QuitRun("q")
            return given
        with mock.patch.object(prompts, "_raw", side_effect=answer), \
                mock.patch.object(prompts, "_emit") as shown, mock.patch.object(prompts, "_retry"):
            signup.ask_on_exit()
        self.options = [call.args[0][0] for call in shown.call_args_list]
        return [call.args[0] for call in output.tell.call_args_list]

    def test_the_ewelink_address_is_offered_first_and_taken_with_enter_or_y(self):
        for typed in ("", "y", "Y", "1"):
            with self.subTest(typed=typed):
                signup.saw_account("me@example.com")
                self.notify.reset_mock()
                self.settings.clear()   # taken by the subtest before
                said = self.answered(typed)
                self.assertEqual(self.options, ["1. Yes, at me@example.com", "2. Use a different address",
                                                "3. No, thank you"])
                self.notify.assert_called_once_with("me@example.com", {}, {})
                self.assertIn("    Check your inbox to confirm.", said)

    def test_n_is_no_thank_you(self):
        signup.saw_account("me@example.com")
        for typed in ("n", "N", "no", "3"):
            with self.subTest(typed=typed):
                self.answered(typed)
        self.notify.assert_not_called()

    def test_q_or_ctrl_c_leave_at_once(self):
        signup.saw_account("me@example.com")
        self.answered("q")
        self.answered(KeyboardInterrupt())
        self.notify.assert_not_called()

    def test_a_different_address_is_typed_and_checked(self):
        signup.saw_account("me@example.com")
        with mock.patch.object(prompts, "ask_text", return_value=" you@example.com ") as typed:
            self.answered("2")
        self.notify.assert_called_once_with("you@example.com", {}, {})
        valid = typed.call_args.kwargs["validate"]
        self.assertTrue(valid("you@example.com")[0])
        self.assertFalse(valid("not an email")[0])
        self.assertEqual(typed.call_args.kwargs["private"], "email")   # kept out of the log

    def test_without_an_email_account_an_address_is_asked_for_and_enter_takes_nothing(self):
        signup.saw_account("+44 7700 900123")   # a phone number's account
        with mock.patch.object(prompts, "ask_text", return_value="me@example.com"):
            self.answered("", "y")   # Enter alone is not an answer here
        self.assertEqual(self.options[:2], ["1. Add an email address", "2. No, thank you"])
        self.notify.assert_called_once()

    def test_the_last_ewelink_address_used_here_is_offered_when_none_was_signed_in_to(self):
        self.settings["ewelink_email"] = "me@example.com"
        self.answered("y")
        self.notify.assert_called_once_with("me@example.com", {}, {})

    def test_it_names_the_accounts_devices_and_the_readouts_sent(self):
        signup.saw_account("me@example.com")
        signup.saw_devices([{"productModel": "S60TPF", "extra": {"model": "SN-S60"}},
                            {"productModel": "S60TPF", "extra": {"model": "SN-S60"}},
                            {"productModel": "MINIR4"}, {"productModel": ""}])
        signup.sent_readout("2026-11-07-0123456789ab")
        signup.sent_readout("")   # one the website had enough of: no reference
        self.answered("y")
        devices, readouts = self.notify.call_args.args[1:]
        self.assertEqual(list(devices), [("S60TPF", "SN-S60"), ("MINIR4", "")])
        self.assertEqual(list(readouts), ["2026-11-07-0123456789ab"])

    def test_not_sent_says_where_to_sign_up_instead_and_asks_again_next_time(self):
        signup.saw_account("me@example.com")
        self.notify.return_value = False
        self.assertIn(f"    That could not be sent just now: sign up at {signup.SIGNUP_PAGE} instead.",
                      self.answered("y"))
        self.assertEqual(signup.given(), "")

    def test_once_an_address_is_taken_it_is_never_asked_for_again(self):
        signup.saw_account("me@example.com")
        self.answered("y")
        self.assertEqual(signup.given(), "me@example.com")
        self.notify.reset_mock()
        output.tell.reset_mock()
        signup.saw_devices([{"productModel": "MINIR4"}])
        signup.sent_readout("2026-11-08-ba9876543210")
        with mock.patch.object(prompts, "ask_choice") as asked:
            signup.ask_on_exit()
        asked.assert_not_called()
        # The sitting's devices and readouts go with it, unasked and unsaid.
        self.notify.assert_called_once_with("me@example.com", {("MINIR4", ""): None},
                                            {"2026-11-08-ba9876543210": None}, update=True)
        output.tell.assert_not_called()

    def test_with_an_address_taken_a_sitting_with_nothing_to_add_sends_nothing(self):
        self.settings[signup.SETTING] = "me@example.com"
        signup.ask_on_exit()
        self.notify.assert_not_called()

    def test_with_an_address_taken_a_send_that_fails_is_only_logged(self):
        self.settings[signup.SETTING] = "me@example.com"
        signup.saw_devices([{"productModel": "MINIR4"}])
        self.notify.return_value = False
        signup.ask_on_exit()
        output.tell.assert_not_called()
        output.log_only.assert_called_once()

    def test_not_asked_after_ctrl_c_unattended_a_closed_window_or_a_test_run(self):
        for why, patch in (("ctrl-c", mock.patch.object(signup.SITTING, "stopped", True)),
                           ("unattended", mock.patch.object(prompts, "UNATTENDED", True)),
                           ("window", mock.patch.object(interrupt, "window_closed", return_value=True)),
                           ("test run", mock.patch.object(readout, "upload_url", return_value=None))):
            with self.subTest(why=why), patch, mock.patch.object(prompts, "ask_choice") as asked:
                signup.ask_on_exit()
                asked.assert_not_called()
        self.notify.assert_not_called()

    def test_stopped_marks_the_sitting(self):
        signup.stopped()
        self.assertTrue(signup.SITTING.stopped)


class ShortcutTests(unittest.TestCase):
    """ask_choice's other answers, which the email question uses."""

    def test_a_shortcut_answers_in_any_case_and_numbers_still_do(self):
        for typed, chosen in (("Y", 0), ("n", 1), ("2", 1)):
            with self.subTest(typed=typed), mock.patch.object(prompts, "_raw", return_value=typed), \
                    mock.patch.object(prompts, "_emit"):
                self.assertEqual(prompts.ask_choice("Q", ["a", "b"], shortcuts={"y": 0, "n": 1}), chosen)


if __name__ == "__main__":
    unittest.main()
