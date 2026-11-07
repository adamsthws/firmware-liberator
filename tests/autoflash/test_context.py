"""A run's context (tools/autoflash/context.py): its eWeLink account and a
password given for it, its Wi-Fi typed only as the fallback, and the port
its phases are served on."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import context, errors, firewall, hostsys, output, prompts, runs, wifi
from tools.autoflash.state import RunState
from tools.espressif.safeboot import prepare_imprinted_bundle


class WifiFallbackTests(unittest.TestCase):
    """Typing the Wi-Fi is the fallback: explained, asked once, and needs no rebuild."""

    def ctx(self, *, found=None, bundle_for=None):
        from types import SimpleNamespace
        ctx = SimpleNamespace(args=SimpleNamespace(wifi=False), manifest_path=Path("unused"),
                              state=SimpleNamespace(set_input=lambda *_: None, input=lambda *_: None,
                                                    is_done=lambda _: False),
                              _wifi=None, _recovery=None, esphome_api_locked=False)
        ctx.remember_wifi = lambda ssid, password, image: context.Context.remember_wifi(
            ctx, ssid, password, image)
        ctx.adopt_wifi = lambda ssid, password: context.Context.adopt_wifi(ctx, ssid, password)
        self.patches = [
            mock.patch.object(wifi, "read_wifi_from_plug", return_value=found),
            mock.patch.object(context, "bundle_ssid", return_value=bundle_for),
            mock.patch.object(output, "say"),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in self.patches])
        return ctx

    def test_details_read_off_the_plug_are_pinned_without_asking_or_downloading(self):
        ctx = self.ctx(found=("Home-2G", "pw"))
        with mock.patch.object(context, "recovery_image",
                               side_effect=prepare_imprinted_bundle.BundleError("no Wi-Fi details yet")), \
             mock.patch.object(context, "pin_wifi", return_value=b"image") as pin, \
             mock.patch.object(context, "build_bundle") as rebuild, \
             mock.patch.object(prompts, "section") as shown, \
             mock.patch.object(context, "ask_credentials") as asked:
            self.assertEqual(context.Context.wifi(ctx), ("Home-2G", "pw"))
        pin.assert_called_once_with(Path("unused"), "Home-2G", "pw")
        rebuild.assert_not_called()
        asked.assert_not_called()
        shown.assert_not_called()

    def test_details_read_off_the_plug_are_not_reported_as_typed(self):
        # A run stopped at the question, then resumed and read off the plug,
        # must not keep the earlier "typed" mark.
        ctx = self.ctx(found=("Home-2G", ""))
        recorded = {}
        ctx.state.set_input = lambda key, value: recorded.__setitem__(key, value)
        with mock.patch.object(context, "recovery_image", return_value=b"image"):
            self.assertEqual(context.Context.wifi(ctx), ("Home-2G", ""))
        self.assertIs(recorded.get("wifi_typed"), False)

    def test_when_they_cannot_be_read_the_reason_is_shown_then_both_are_asked(self):
        ctx = self.ctx(found=None, bundle_for=None)
        with mock.patch.object(context, "recovery_image",
                               side_effect=prepare_imprinted_bundle.BundleError("no Wi-Fi details yet")), \
             mock.patch.object(context, "pin_wifi", return_value=b"image"), \
             mock.patch.object(prompts, "section") as shown, \
             mock.patch.object(context, "ask_credentials", return_value=("Home-2G", "pw")) as asked:
            details = context.Context.wifi(ctx, why=["Tasmota is waiting on its setup network."])
        self.assertEqual(details, ("Home-2G", "pw"))
        asked.assert_called_once()
        self.assertIn("Tasmota is waiting on its setup network.", shown.call_args.args[1])

    def test_a_bundle_the_checkout_cannot_use_is_built_again(self):
        ctx = self.ctx(found=("Home-2G", "pw"))
        with mock.patch.object(context, "recovery_image", side_effect=prepare_imprinted_bundle.BundleError("x")), \
             mock.patch.object(context, "pin_wifi", side_effect=prepare_imprinted_bundle.BundleError("old")), \
             mock.patch.object(context, "build_bundle", return_value=(Path("m"), b"image")) as rebuild:
            context.Context.wifi(ctx)
        rebuild.assert_called_once()


class EwelinkAccountTests(unittest.TestCase):
    """The last eWeLink account is offered, not assumed."""

    def email(self, *, remembered=None, chosen=None, choice=0, typed="new@example.com", given=None):
        from types import SimpleNamespace
        inputs = {"email": chosen} if chosen else {}
        ctx = SimpleNamespace(state=SimpleNamespace(input=inputs.get,
                                                    set_input=inputs.__setitem__),
                              remember_email=lambda e: inputs.__setitem__("email", e),
                              _given_email=given)
        with mock.patch.object(runs, "load_settings",
                               return_value={"ewelink_email": remembered} if remembered else {}), \
             mock.patch.object(prompts, "ask_choice", return_value=choice) as menu, \
             mock.patch.object(prompts, "ask_text", return_value=typed) as asked:
            return context.Context.email(ctx), menu, asked

    def test_last_time_s_account_is_offered_and_can_be_kept(self):
        email, menu, asked = self.email(remembered="old@example.com", choice=0)
        self.assertEqual(email, "old@example.com")
        self.assertIn("old@example.com", menu.call_args.args[1][0])
        asked.assert_not_called()

    def test_a_different_account_can_be_entered(self):
        email, _, asked = self.email(remembered="old@example.com", choice=1)
        self.assertEqual(email, "new@example.com")
        asked.assert_called_once()

    def test_one_given_in_advance_is_used_without_asking(self):
        email, menu, asked = self.email(remembered="old@example.com", given="env@example.com")
        self.assertEqual(email, "env@example.com")
        menu.assert_not_called()
        asked.assert_not_called()

    def test_with_none_remembered_it_is_simply_asked(self):
        email, menu, _ = self.email()
        self.assertEqual(email, "new@example.com")
        menu.assert_not_called()

    def test_once_chosen_in_a_run_it_is_not_asked_again(self):
        email, menu, asked = self.email(remembered="old@example.com", chosen="run@example.com")
        self.assertEqual(email, "run@example.com")
        menu.assert_not_called()
        asked.assert_not_called()


class PhasePortTests(unittest.TestCase):
    """The migration steps are served on 8089 unless something else has it."""

    def runner(self, busy, delivered="8088"):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            state = RunState.open(Path(directory))
            state.set_input("delivery_port", delivered)
            ctx = SimpleNamespace(state=state, manifest_path=Path("m.json"), evidence=Path("e"),
                                  listen_ip=lambda: "192.0.2.141", device=object(),
                                  recovery_bytes=lambda: None)
            opened = []
            with mock.patch.object(hostsys, "port_is_free", lambda ip, port: port not in busy), \
                 mock.patch.object(firewall, "ensure_firewall",
                                   lambda c: opened.append(firewall.firewall_ports(c))), \
                 mock.patch.object(context, "PhaseRunner") as runner, \
                 mock.patch.object(output, "say"):
                try:
                    context.Context.phase_runner(ctx)
                    error = None
                except errors.Abort as exc:
                    error = str(exc)
            port = runner.call_args.kwargs["listen_port"] if runner.called else None
            return port, opened, state.input("phase_port"), error

    def test_8089_is_used_when_free(self):
        self.assertEqual(self.runner(busy=set())[:3], (8089, [[8088, 8089]], "8089"))

    def test_a_busy_8089_moves_to_8100_and_the_firewall_follows(self):
        # The bridge may have been served on 8090-8099; the phases stay clear of those.
        self.assertEqual(self.runner(busy={8089}, delivered="8090")[:3], (8100, [[8090, 8100]], "8100"))

    def test_every_port_busy_stops_before_anything_is_sent(self):
        port, opened, _, error = self.runner(busy={8089, *range(8100, 8110)})
        self.assertIsNone(port)
        self.assertEqual(opened, [])
        self.assertIn("all in use", error)


class GivenPasswordTests(unittest.TestCase):
    def context(self, given, email="me@example.com", given_email=None):
        import tools.autoflash.cli as autoflash
        ctx = SimpleNamespace(_ewelink_password=None, _given_ewelink_password=given,
                              _given_email=given_email, email=lambda: email)
        quiet = mock.patch.object(output, "say")
        quiet.start()
        self.addCleanup(quiet.stop)
        return autoflash, ctx

    def test_it_is_used_once_then_asked_for(self):
        autoflash, ctx = self.context("from-env")
        with mock.patch.object(prompts, "ask_secret", return_value="typed") as asked:
            self.assertEqual(context.Context.ewelink_env(ctx)["EWELINK_PASSWORD"], "from-env")
            asked.assert_not_called()
            ctx._ewelink_password = None  # eWeLink refused it
            self.assertEqual(context.Context.ewelink_env(ctx)["EWELINK_PASSWORD"], "typed")

    def test_not_for_another_account_than_the_one_given(self):
        autoflash, ctx = self.context("from-env", email="other@example.com",
                                      given_email="me@example.com")
        with mock.patch.object(prompts, "ask_secret", return_value="typed"):
            self.assertEqual(context.Context.ewelink_env(ctx)["EWELINK_PASSWORD"], "typed")


if __name__ == "__main__":
    unittest.main()
