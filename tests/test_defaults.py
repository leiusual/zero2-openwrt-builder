"""Exercise actual OpenWrt UCI, board detection and boot scripts in isolation.

Set ZERO2_TEST_ROOTFS to an extracted rootfs with this project's files.
ZERO2_TEST_PROOT and ZERO2_TEST_QEMU can override proot/static qemu paths.
Hardware radio probing is represented by a generated wireless configuration.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = 'etc/uci-defaults/99-zero2-defaults'


@unittest.skipUnless(os.environ.get('ZERO2_TEST_ROOTFS'), 'requires firmware rootfs')
class DefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'root'
        shutil.copytree(os.environ['ZERO2_TEST_ROOTFS'], self.root, symlinks=True)
        self.assertEqual((self.root / SCRIPT).read_bytes(), (ROOT / 'files' / SCRIPT).read_bytes())
        self.assertFalse((self.root / SCRIPT).stat().st_mode & 0o111)
        self.runner = [os.environ.get('ZERO2_TEST_PROOT', 'proot'), '-r', str(self.root),
                       '-q', os.environ.get('ZERO2_TEST_QEMU', 'qemu-aarch64-static'),
                       '-b', '/dev', '-b', '/proc', '-w', '/']
        # Isolate this script from unrelated package migrations, but use the
        # real boot service to execute, commit and remove successful scripts.
        for p in (self.root / 'etc/uci-defaults').iterdir():
            if p.name != '99-zero2-defaults':
                p.unlink()
        self.guest('''
            mkdir -p /tmp/sysinfo /tmp/.uci /var/state
            echo friendlyarm,nanopi-zero2 > /tmp/sysinfo/board_name
            echo 'FriendlyElec NanoPi Zero2' > /tmp/sysinfo/model
            /bin/board_detect /tmp/board.json
            boot_hook_add() { :; }
            . /lib/preinit/82_config_generate
            do_config_generate
        ''')

    def guest(self, script):
        result = subprocess.run(self.runner + ['/bin/sh', '-c', script], text=True,
                                capture_output=True, timeout=60,
                                env=dict(os.environ, PROOT_NO_SECCOMP='1'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def add_wifi(self):
        (self.root / 'etc/config/wireless').write_text('''
config wifi-device 'radio0'
    option type 'mac80211'
    option path 'platform/test-pcie/0000:01:00.0'
    option band '5g'
    option channel '36'
    option htmode 'VHT80'
config wifi-iface 'default_radio0'
    option device 'radio0'
    option network 'lan'
    option mode 'ap'
    option ssid 'OpenWrt'
    option encryption 'none'
    option disabled '1'
''')

    def boot_defaults(self):
        self.guest('. /etc/init.d/boot; uci_apply_defaults')

    def snapshot(self):
        return {p.name: p.read_bytes() for p in (self.root / 'etc/config').iterdir() if p.is_file()}

    def test_fresh_boot_topology_and_dhcp_privacy(self):
        self.guest('''
            set -e
            [ "$(uci get network.wan.device)" = eth0 ]
            [ "$(uci get network.wan.proto)" = dhcp ]
            ! uci -q get network.lan
        ''')
        untouched = {name: (self.root / 'etc/config' / name).read_bytes() for name in ('dhcp', 'firewall')}
        self.add_wifi()
        self.boot_defaults()
        self.assertFalse((self.root / SCRIPT).exists())
        expected = {
            'network.lan.device': 'br-lan', 'network.lan.proto': 'static',
            'network.lan.ipaddr': '192.168.2.1/24', 'network.br_lan.name': 'br-lan',
            'network.br_lan.type': 'bridge', 'network.br_lan.bridge_empty': '1',
            'network.wan.device': 'eth0', 'network.wan.proto': 'dhcp',
            'network.wan.hostname': '*', 'network.wan6.device': 'eth0',
            'network.wan6.proto': 'dhcpv6', 'network.wan6.noclientfqdn': '1',
            'system.@system[0].hostname': 'Zero', 'system.@system[0].zero2_defaults_version': '1',
            'system.@system[0].zonename': 'Asia/Singapore', 'system.@system[0].timezone': '<+08>-8',
            'wireless.radio0.country': 'CN', 'wireless.radio0.disabled': '0',
            'wireless.radio0.path': 'platform/test-pcie/0000:01:00.0',
            'wireless.radio0.band': '5g', 'wireless.radio0.channel': '36', 'wireless.radio0.htmode': 'VHT80',
            'wireless.default_radio0.ssid': 'Zero', 'wireless.default_radio0.network': 'lan',
            'wireless.default_radio0.encryption': 'none', 'wireless.default_radio0.disabled': '0',
        }
        values = self.guest('\n'.join(f'uci get {key}' for key in expected)).splitlines()
        self.assertEqual(dict(zip(expected, values)), expected)
        self.guest('! uci -q get network.br_lan.ports')
        for name, data in untouched.items():
            self.assertEqual((self.root / 'etc/config' / name).read_bytes(), data)
        # Run the real protocol handlers, replacing only client launch/ubus.
        args4 = self.guest('''
            cd /lib/netifd/proto
            set -- dhcp test
            add_protocol() { :; }
            . ./dhcp.sh
            proto_run_command() { printf '%s\\n' "$@"; }
            json_init
            json_add_string hostname "$(uci get network.wan.hostname)"
            json_add_string sendclientid hardware
            proto_dhcp_setup wan eth0
        ''').splitlines()
        self.assertIn('udhcpc', args4)
        self.assertFalse(any(arg.startswith('hostname:') for arg in args4))
        args6 = self.guest('''
            cd /lib/netifd/proto
            set -- dhcpv6 test
            add_protocol() { :; }
            . ./dhcpv6.sh
            proto_run_command() { printf '%s\\n' "$@"; }
            ubus() { echo '{}'; }
            json_init
            json_add_string noclientfqdn "$(uci get network.wan6.noclientfqdn)"
            json_add_string sendclientid hardware
            json_add_string reqprefix no
            proto_dhcpv6_setup wan6 eth0
        ''').splitlines()
        self.assertIn('odhcp6c', args6)
        self.assertIn('-f', args6)

    def test_preserved_configuration_is_not_reset_on_upgrade(self):
        self.add_wifi()
        self.boot_defaults()
        self.guest('''
            uci set network.lan.ipaddr=10.42.0.1/24
            uci set network.wan.proto=pppoe
            uci set network.wan.username=example
            uci set system.@system[0].hostname=MyRouter
            uci set wireless.default_radio0.ssid=MyWifi
            uci set wireless.default_radio0.encryption=sae
            uci set wireless.default_radio0.key=example-test-key
            uci commit
        ''')
        before = self.snapshot()
        # A new image reinstalls uci-defaults while sysupgrade restores UCI.
        shutil.copy2(ROOT / 'files' / SCRIPT, self.root / SCRIPT)
        self.boot_defaults()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / SCRIPT).exists())

    def test_missing_radio_keeps_script_for_next_boot(self):
        self.boot_defaults()
        self.assertTrue((self.root / SCRIPT).exists())
        self.guest("! uci -q get 'system.@system[0].zero2_defaults_version'")
        self.add_wifi()
        self.boot_defaults()
        self.assertFalse((self.root / SCRIPT).exists())
        self.assertEqual(self.guest('uci get network.br_lan.bridge_empty').strip(), '1')


if __name__ == '__main__':
    unittest.main()
