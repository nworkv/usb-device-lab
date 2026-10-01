import io
import json
import unittest
from pathlib import Path
from unittest import mock
from usb_device_lab.doctor import gadget_report,host_report,main

class DoctorTests(unittest.TestCase):
    def test_gadget_with_one_udc(self):
        with mock.patch('usb_device_lab.doctor.Path.exists',return_value=True),\
             mock.patch('usb_device_lab.doctor.Path.is_dir',return_value=True),\
             mock.patch('usb_device_lab.doctor.Path.glob',return_value=[Path('/sys/class/udc/test')]),\
             mock.patch('usb_device_lab.doctor.readlink_name',return_value='dwc3-gadget'),\
             mock.patch('usb_device_lab.doctor.Path.read_text',return_value='not attached\n'):
            r=gadget_report();self.assertTrue(r['ready']);self.assertEqual(r['selected_udc'],'test');self.assertEqual(r['udc_driver'],'dwc3-gadget')
    def test_gadget_requires_selection_for_multiple_udcs(self):
        with mock.patch('usb_device_lab.doctor.Path.exists',return_value=True),\
             mock.patch('usb_device_lab.doctor.Path.glob',return_value=[Path('a'),Path('b')]):
            r=gadget_report();self.assertFalse(r['ready']);self.assertIn('select',r['udc']['detail'])
    def test_host_requires_debugfs_kcov_and_kmsg(self):
        with mock.patch('usb_device_lab.doctor.Path.exists',side_effect=lambda:False),\
             mock.patch('usb_device_lab.doctor.Path.is_dir',return_value=False):
            r=host_report(1);self.assertFalse(r['ready']);self.assertTrue(r['bus']['ok'])
    def test_host_rejects_invalid_bus(self):
        with mock.patch('usb_device_lab.doctor.Path.exists',return_value=True),\
             mock.patch('usb_device_lab.doctor.Path.is_dir',return_value=True):
            r=host_report(0);self.assertFalse(r['ready']);self.assertFalse(r['bus']['ok'])
    def test_main_is_json_and_nonzero_when_not_ready(self):
        out=io.StringIO()
        with mock.patch('usb_device_lab.doctor.host_report',return_value={'ready':False,'role':'host'}),\
             mock.patch('sys.stdout',out): self.assertEqual(main(['host']),1)
        self.assertFalse(json.loads(out.getvalue())['ready'])
if __name__=='__main__': unittest.main()
