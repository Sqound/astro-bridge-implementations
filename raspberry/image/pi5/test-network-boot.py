#!/usr/bin/env python3
"""Offline checks for the Pi 5 network boot overlay and USB event scope."""
import importlib.util, pathlib, re, tempfile, unittest
HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('recipe',HERE/'prepare-gphoto.py')
recipe=importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)

class NetworkBoot(unittest.TestCase):
 def test_overlay_installs_forwarding_rule_and_service(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d); (root/'etc/init.d').mkdir(parents=True)
   recipe.install_network_boot(root)
   self.assertEqual((root/'etc/sysctl.d/80-wifi-bridge.conf').read_text(),'net.ipv4.ip_forward=1\nnet.ipv4.conf.all.arp_ignore=1\n')
   rule=root/'etc/udev/rules.d/39-astro-realtek-switch.rules'
   self.assertEqual(rule.read_bytes(),(HERE/rule.name).read_bytes())
   self.assertEqual(rule.stat().st_mode & 0o777,0o644)
   service=root/'etc/init.d/astro-target-udev'
   self.assertEqual(service.read_bytes(),(HERE/service.name).read_bytes())
   self.assertEqual(service.stat().st_mode & 0o777,0o755)
 def test_rule_identity_and_final_dispatch(self):
  rule=(HERE/'39-astro-realtek-switch.rules').read_text().splitlines()[-1]
  matches=dict(re.findall(r'(ACTION|SUBSYSTEM|ENV\{DEVTYPE\}|ATTR\{[^}]+\})=="([^"]+)"',rule))
  expected={'ACTION':'add','SUBSYSTEM':'usb','ENV{DEVTYPE}':'usb_device','ATTR{idVendor}':'0bda','ATTR{idProduct}':'1a2b','ATTR{bcdDevice}':'0200','ATTR{manufacturer}':'Realtek','ATTR{product}':'DISK'}
  self.assertEqual(matches,expected)
  devices=[dict(expected, **{'ATTR{busnum}':'003','ATTR{devnum}':'006'}),dict(expected, **{'ATTR{busnum}':'003','ATTR{devnum}':'007'})]
  self.assertTrue(all(all(d[k]==v for k,v in matches.items()) for d in devices))
  for key in expected:
   altered=dict(devices[0]); altered[key]='other'
   self.assertFalse(all(altered[k]==v for k,v in matches.items()))
  self.assertIn('RUN:="/bin/busybox timeout 8 /usr/sbin/usb_modeswitch -v 0x0bda -p 0x1a2b -b $attr{busnum} -g $attr{devnum} -K"',rule)
 def test_runtime_order_and_bounded_replay(self):
  service=(HERE/'astro-target-udev').read_text()
  self.assertIn('need astro-firstboot; before networkmanager',service)
  self.assertLess(service.index('udevadm control --reload'),service.index('--subsystem-match=usb'))
  for match in ('idVendor=0bda','idProduct=1a2b','bcdDevice=0200','manufacturer=Realtek','product=DISK'): self.assertIn('--attr-match='+match,service)
  self.assertIn('udevadm settle --timeout=10 || :',service)
  self.assertLess(service.index('--subsystem-match=usb'),service.index('--subsystem-match=net'))
 def test_onboard_ethernet_replay_never_blocks_boot(self):
  service=(HERE/'astro-target-udev').read_text()
  line=next(l for l in service.splitlines() if '--sysname-match=eth0' in l)
  self.assertTrue(line.rstrip().endswith('|| :;;'))
  self.assertLess(service.index('--sysname-match=wlan0'),service.index('--sysname-match=eth0'))
  self.assertLess(service.index('--sysname-match=eth0'),service.rindex('udevadm settle'))
if __name__=='__main__': unittest.main()
