import os
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from droid_tier import schedule

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class WindowsTaskTest(unittest.TestCase):
    def test_xml_is_valid_and_runs_hidden_on_battery(self):
        xml = schedule.task_xml(r"C:\Users\Ana & Bia\venv\Scripts\pythonw.exe", r"PC\ana")
        root = ET.fromstring(xml.split("?>", 1)[1])  # ET nao aceita a declaracao UTF-16 em str
        get = lambda path: root.find(path, NS).text  # noqa: E731
        self.assertEqual(get("t:Actions/t:Exec/t:Command"), r"C:\Users\Ana & Bia\venv\Scripts\pythonw.exe")
        self.assertEqual(get("t:Actions/t:Exec/t:Arguments"), "-m droid_tier run")
        self.assertEqual(get("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval"), "PT5M")
        self.assertEqual(get("t:Principals/t:Principal/t:UserId"), r"PC\ana")
        self.assertEqual(get("t:Principals/t:Principal/t:LogonType"), "InteractiveToken")
        self.assertEqual(get("t:Settings/t:DisallowStartIfOnBatteries"), "false")
        self.assertEqual(get("t:Settings/t:StopIfGoingOnBatteries"), "false")
        self.assertEqual(get("t:Settings/t:MultipleInstancesPolicy"), "IgnoreNew")

    def test_current_user(self):
        with mock.patch.dict(os.environ, {"USERDOMAIN": "PC", "USERNAME": "ana"}):
            self.assertEqual(schedule.current_user(), "PC\\ana")

    @unittest.skipUnless(os.name == "nt", "so no Windows")
    def test_prefers_pythonw(self):
        self.assertTrue(schedule.python_for_task().lower().endswith("pythonw.exe"))


class LinuxUnitTest(unittest.TestCase):
    def test_units(self):
        self.assertIn("ExecStart=/opt/py/bin/python -m droid_tier run", schedule.service_unit("/opt/py/bin/python"))
        self.assertIn("OnUnitActiveSec=5min", schedule.timer_unit())


if __name__ == "__main__":
    unittest.main()
