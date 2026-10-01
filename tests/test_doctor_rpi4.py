import gzip
import os
import tempfile
import unittest
from pathlib import Path
from usb_device_lab import doctor

class Rpi4Tests(unittest.TestCase):
    def make(self,model='Raspberry Pi 4 Model B Rev 1.4',mode=b'peripheral\0',overlay='dtoverlay=dwc2,dr_mode=peripheral\n',
             udc='fe980000.usb',driver='dwc2',raw=True,config=b'CONFIG_USB_RAW_GADGET=m\n'):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);r=Path(tmp.name)
        dt=r/'proc/device-tree';(dt/'soc/usb@7e980000').mkdir(parents=True)
        (dt/'model').write_bytes(model.encode()+b'\0')
        if mode is not None: (dt/'soc/usb@7e980000/dr_mode').write_bytes(mode)
        (r/'boot/firmware').mkdir(parents=True);(r/'boot/firmware/config.txt').write_text('[all]\n'+overlay)
        (r/'sys/class/udc').mkdir(parents=True)
        if udc:
            dev=r/'sys/devices/platform/soc'/udc;(dev).mkdir(parents=True);(r/'sys/bus/platform/drivers'/driver).mkdir(parents=True)
            os.symlink(r/'sys/bus/platform/drivers'/driver,dev/'driver')
            (r/'sys/class/udc'/udc).mkdir();os.symlink(dev,r/'sys/class/udc'/udc/'device');(r/'sys/class/udc'/udc/'state').write_text('not attached\n')
        (r/'dev').mkdir()
        if raw: (r/'dev/raw-gadget').write_text('')
        if config is not None: (r/'proc/config.gz').write_bytes(gzip.compress(config))
        return r
    def test_ready_pi(self):
        rep=doctor.rpi4_report(root=self.make(),release='6.6.0');self.assertTrue(rep['ready'],rep);self.assertEqual(rep['udc'],'fe980000.usb')
    def test_host_mode_fails(self):
        rep=doctor.rpi4_report(root=self.make(mode=b'host\0'),release='6.6.0')
        self.assertFalse(rep['ready']);self.assertFalse(rep['checks']['dr_mode']['ok']);self.assertIn('dtoverlay=dwc2',rep['advice'])
    def test_missing_udc_and_raw_gadget(self):
        rep=doctor.rpi4_report(root=self.make(udc=None,raw=False,overlay=''),release='6.6.0')
        for k in ('usb_c_udc','dwc2_driver','raw_gadget_node','dwc2_overlay'): self.assertFalse(rep['checks'][k]['ok'],k)
    def test_kernel_option_not_set(self):
        rep=doctor.rpi4_report(root=self.make(config=b'# CONFIG_USB_RAW_GADGET is not set\n'),release='6.6.0')
        self.assertFalse(rep['checks']['CONFIG_USB_RAW_GADGET']['ok']);self.assertIn('n from',rep['checks']['CONFIG_USB_RAW_GADGET']['detail'])
    def test_kernel_option_from_boot_config_and_module(self):
        r=self.make(config=None);(r/'boot/config-6.6.0').write_text('CONFIG_USB_RAW_GADGET=y\n')
        self.assertEqual(doctor.kernel_config_option('CONFIG_USB_RAW_GADGET',r,'6.6.0')[0],'y')
        r=self.make(config=None);(r/'sys/module/raw_gadget').mkdir(parents=True)
        self.assertEqual(doctor.kernel_config_option('CONFIG_USB_RAW_GADGET',r,'6.6.0')[0],'m')
    def test_wrong_driver_and_board(self):
        rep=doctor.rpi4_report(root=self.make(model='Raspberry Pi 3 Model B',driver='musb-hdrc'),release='6.6.0')
        self.assertFalse(rep['checks']['model']['ok']);self.assertFalse(rep['checks']['dwc2_driver']['ok'])

if __name__=='__main__': unittest.main()
