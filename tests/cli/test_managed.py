import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from mpv_enhanced import VERSION
from mpv_enhanced.cli import main
from mpv_enhanced.install.receipt import InstallationReceipt, InstalledComponent
from tests.support import ROOT, WINDOWS, Terminal
from tests.versions.support import ManagerTestCase


class ManagedCliTests(ManagerTestCase):
    def setUp(self) -> None:
        super().setUp()
        environment = patch.dict("os.environ", {"MPVE_ROOT": str(self.layout.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def run_cli(
        self, *arguments: str, cache: Path | None = None, stdin: StringIO | None = None
    ) -> tuple[int, str, str]:
        output = StringIO()
        errors = StringIO()
        code = main(
            [
                arguments[0],
                "--manifest",
                str(ROOT / "manifest.toml"),
                "--platform",
                WINDOWS.key,
                *arguments[1:],
            ],
            stdin=stdin or StringIO(),
            stdout=output,
            stderr=errors,
        )
        return code, output.getvalue(), errors.getvalue()

    def optional_chosen(
        self, *arguments: str, stdin: StringIO | None = None
    ) -> tuple[dict[str, object], str]:
        """Run an install with mpvfin already active and return the install options."""
        app = self.layout.app("0.1.0")
        app.mkdir(parents=True)
        InstallationReceipt.create(
            release="0.1.0",
            profile="core",
            platform=WINDOWS.key,
            components=(InstalledComponent("mpvfin", "0.1.0", "download"),),
        ).write(app)
        self.layout.activate("0.1.0")

        def install(_profile, target, **_kwargs):
            return type(
                "Result",
                (),
                {
                    "receipt": type("R", (), {"release": VERSION, "profile": "core"}),
                    "install_dir": Path(target),
                    "previous_dir": None,
                },
            )

        with (
            patch("mpv_enhanced.cli.install.ArtifactCache"),
            patch("mpv_enhanced.cli.install.Installer") as installer,
        ):
            installer.return_value.install.side_effect = install
            code, output, errors = self.run_cli(*arguments, stdin=stdin)
        self.assertEqual(code, 0, errors)
        return installer.return_value.install.call_args.kwargs, output

    def test_updates_keep_optional_components_without_asking(self) -> None:
        options, output = self.optional_chosen(
            "install", "--profile", "core", stdin=Terminal("")
        )
        self.assertEqual(options["optional"], ("mpvfin",))
        self.assertNotIn("Choose optional components", output)

    def test_changing_profile_shows_the_screen_with_what_is_installed_marked(
        self,
    ) -> None:
        options, output = self.optional_chosen(
            "profile", "core", stdin=Terminal("\n1\n")
        )
        self.assertEqual(options["optional"], ())
        self.assertIn("Choose optional components", output)
        self.assertIn("1. [x] mpvfin — ", output)

    def test_with_flag_replaces_what_is_installed(self) -> None:
        options, _ = self.optional_chosen("install", "--profile", "core", "--with", "x")
        self.assertEqual(options["optional"], ("x",))

    def test_a_wrong_server_url_is_asked_again(self) -> None:
        options, output = self.optional_chosen(
            "profile",
            "core",
            stdin=Terminal("\n\njellyfin.local\nhttp://jellyfin.local:8096\n"),
        )
        self.assertEqual(options["answers"], {"mpvfin": "http://jellyfin.local:8096"})
        self.assertIn("doesn't look like", output)

    def test_install_goes_to_the_versioned_folder_and_activates_it(self) -> None:
        def install(_profile, target, **_kwargs):
            Path(target).mkdir(parents=True)
            previous = Path(target).with_name("app.previous")
            previous.mkdir()
            return type(
                "Result",
                (),
                {
                    "receipt": type("R", (), {"release": VERSION, "profile": "core"}),
                    "install_dir": Path(target),
                    "previous_dir": previous,
                },
            )

        with (
            patch("mpv_enhanced.cli.install.ArtifactCache"),
            patch("mpv_enhanced.cli.install.Installer") as installer,
        ):
            installer.return_value.install.side_effect = install
            code, output, errors = self.run_cli(
                "install",
                "--profile",
                "core",
                "--cache-dir",
                str(self.layout.root / "cache"),
            )
        self.assertEqual(code, 0, errors)
        call = installer.return_value.install.call_args
        self.assertEqual(call.args[1], self.layout.app(VERSION))
        self.assertEqual(self.layout.active(), VERSION)
        self.assertFalse(self.layout.app(VERSION).with_name("app.previous").exists())
        self.assertIn(
            f"Start mpv-enhanced with: {self.layout.player(VERSION, WINDOWS)}",
            output,
        )

    def test_profile_needs_an_existing_install(self) -> None:
        code, _, errors = self.run_cli("profile", "core")
        self.assertEqual(code, 2)
        self.assertIn("nothing is installed yet", errors)

    def test_status_and_rollback(self) -> None:
        self.install("0.1.0")
        self.install("0.2.0", "rtx-high")
        self.layout.activate("0.2.0")
        code, output, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("mpv-enhanced 0.2.0", output)
        self.assertIn("Profile: rtx-high", output)
        self.assertIn("Previous version: 0.1.0", output)
        code, output, _ = self.run_cli("rollback")
        self.assertEqual(code, 0)
        self.assertIn("Now using 0.1.0", output)


if __name__ == "__main__":
    unittest.main()
