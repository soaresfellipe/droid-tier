import contextlib
import io
import os
import sys
import unittest
from unittest import mock

import droid_tier.update as u


class VersionTupleTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(u.version_tuple("0.6.0"), (0, 6, 0))
        self.assertGreater(u.version_tuple("0.10.0"), u.version_tuple("0.9.9"))

    def test_ignores_non_numeric_parts(self):
        self.assertEqual(u.version_tuple("0.7.0rc1"), (0, 7, 0))


class RunTest(unittest.TestCase):
    def out(self, fn, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fn(*args)
        return code, buf.getvalue()

    def test_check_same_version(self):
        with mock.patch.object(u, "remote_version", return_value=u.__version__):
            code, text = self.out(u.run, ["--check"])
        self.assertEqual(code, 0)
        self.assertIn("up to date", text)

    def test_check_newer_version(self):
        with mock.patch.object(u, "remote_version", return_value="9.9.9"):
            code, text = self.out(u.run, ["--check"])
        self.assertEqual(code, 1)
        self.assertIn("update available", text)

    def test_remote_failure(self):
        with mock.patch.object(u, "remote_version", side_effect=OSError("no net")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = u.run(["--check"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't fetch", err.getvalue())

    def test_same_version_is_a_noop_without_force(self):
        with mock.patch.object(u, "remote_version", return_value=u.__version__), \
                mock.patch.object(u, "install_cmd") as cmd:
            code, text = self.out(u.run, [])
        self.assertEqual(code, 0)
        self.assertFalse(cmd.called)

    def test_dev_checkout_is_refused(self):
        with mock.patch.object(u, "remote_version", return_value="9.9.9"), \
                mock.patch.object(u, "install_cmd", return_value=None), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = u.run([])
        self.assertEqual(code, 1)
        self.assertIn("source checkout", err.getvalue())


class InstallCmdTest(unittest.TestCase):
    def test_dev_checkout(self):
        with mock.patch.object(u, "in_dev_checkout", return_value=True):
            self.assertIsNone(u.install_cmd())

    def test_uv_tool(self):
        with mock.patch.object(u, "in_dev_checkout", return_value=False), \
                mock.patch.object(u, "in_uv_tools", return_value=True), \
                mock.patch.object(u, "in_pipx", return_value=False):
            self.assertEqual(u.install_cmd(), ["uv", "tool", "install", "--force", f"git+{u.REPO_URL}"])

    def test_pipx(self):
        with mock.patch.object(u, "in_dev_checkout", return_value=False), \
                mock.patch.object(u, "in_uv_tools", return_value=False), \
                mock.patch.object(u, "in_pipx", return_value=True):
            self.assertEqual(u.install_cmd(), ["pipx", "upgrade", "droid-tier"])

    def test_plain_pip(self):
        with mock.patch.object(u, "in_dev_checkout", return_value=False), \
                mock.patch.object(u, "in_uv_tools", return_value=False), \
                mock.patch.object(u, "in_pipx", return_value=False):
            self.assertEqual(u.install_cmd(), [sys.executable, "-m", "pip", "install", "--upgrade",
                                               f"git+{u.REPO_URL}"])


if __name__ == "__main__":
    unittest.main()
