"""Exercise actual OpenWrt UCI, board detection and boot scripts in isolation.

Set ZERO2_TEST_ROOTFS to an extracted rootfs with this project's files.
ZERO2_TEST_PROOT and ZERO2_TEST_QEMU can override proot/static qemu paths.
Only radio capabilities are simulated; official mac80211.uc generates UCI.
"""
import os
import json
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
                       '-b', '/dev/null', '-b', '/dev/zero', '-b', '/dev/urandom',
                       '-b', '/dev/random', '-b', '/proc', '-w', '/']
        # Keep the image's complete uci-defaults sequence, including 15_odhcpd:
        # it derives DHCP server mode from board.json before our script runs.
        self.assertTrue((self.root / 'etc/uci-defaults/15_odhcpd').is_file())
        self.guest('''
            mkdir -p /tmp/sysinfo /tmp/.uci /var/state /var/lock
            echo friendlyarm,nanopi-zero2 > /tmp/sysinfo/board_name
            echo 'FriendlyElec NanoPi Zero2' > /tmp/sysinfo/model
            /bin/board_detect /tmp/board.json
            boot_hook_add() { :; }
            . /lib/preinit/82_config_generate
            do_config_generate
        ''')

    def guest(self, script):
        result = subprocess.run(self.runner + ['/bin/sh', '-c', script], text=True,
                                capture_output=True, timeout=120,
                                env=dict(os.environ, PROOT_NO_SECCOMP='1'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def add_wifi(self):
        board_path = self.root / 'etc/board.json'
        board = json.loads(board_path.read_text())
        board['wlan'] = {'phy0': {
            'path': 'platform/test-pcie/0000:01:00.0',
            'info': {'bands': {
                '2G': {'ht': True, 'max_width': 40, 'default_channel': 1},
                '5G': {'ht': True, 'vht': True, 'max_width': 80, 'default_channel': 36},
            }, 'radios': []},
        }}
        board_path.write_text(json.dumps(board))
        macfile = self.root / 'sys/class/ieee80211/phy0/macaddress'
        macfile.parent.mkdir(parents=True)
        macfile.write_text('02:00:00:00:00:01\n')
        self.guest('''
            set -e
            touch /etc/config/wireless
            ucode /lib/wifi/mac80211.uc > /tmp/generated-wireless.uci
            uci -q batch < /tmp/generated-wireless.uci
        ''')

    def boot_defaults(self):
        self.guest('. /etc/init.d/boot; uci_apply_defaults')

    def test_fresh_boot_topology_and_dhcp_privacy(self):
        self.guest('''
            set -e
            [ "$(uci get network.wan.device)" = eth0 ]
            [ "$(uci get network.wan.proto)" = dhcp ]
            ! uci -q get network.lan
        ''')
        board = json.loads((self.root / 'etc/board.json').read_text())
        self.assertEqual(board['network']['lan'], {'protocol': 'static'})
        firewall = (self.root / 'etc/config/firewall').read_bytes()
        self.add_wifi()
        self.boot_defaults()
        self.assertFalse((self.root / SCRIPT).exists())
        expected = {
            'network.lan.device': 'br-lan', 'network.lan.proto': 'static',
            'network.lan.ipaddr': '192.168.2.1/24', 'network.br_lan.name': 'br-lan',
            'network.br_lan.type': 'bridge', 'network.br_lan.bridge_empty': '1',
            'network.wan': 'interface', 'network.wan6': 'interface',
            'network.wan.device': 'eth0', 'network.wan.proto': 'dhcp',
            'network.wan.hostname': '*', 'network.wan6.device': 'eth0',
            'network.wan6.proto': 'dhcpv6', 'network.wan6.noclientfqdn': '1',
            'dhcp.lan.interface': 'lan', 'dhcp.lan.dhcpv4': 'server',
            'dhcp.lan.dhcpv6': 'server', 'dhcp.lan.ra': 'server',
            'dhcp.lan.start': '100', 'dhcp.lan.limit': '150', 'dhcp.lan.leasetime': '12h',
            'system.@system[0].hostname': 'Zero',
            'system.@system[0].zonename': 'Asia/Singapore', 'system.@system[0].timezone': '<+08>-8',
            'wireless.radio0.country': 'CN',
            'wireless.radio0.path': 'platform/test-pcie/0000:01:00.0',
            'wireless.radio0.band': '5g', 'wireless.radio0.channel': '36', 'wireless.radio0.htmode': 'VHT80',
            'wireless.default_radio0.ssid': 'Zero', 'wireless.default_radio0.network': 'lan',
            'wireless.default_radio0.mode': 'ap',
            'wireless.default_radio0.encryption': 'none', 'wireless.default_radio0.disabled': '0',
        }
        values = self.guest('\n'.join(f'uci get {key}' for key in expected)).splitlines()
        self.assertEqual(dict(zip(expected, values)), expected)
        self.guest('! uci -q get network.br_lan.ports')
        self.guest('! uci -q get wireless.radio0.disabled')
        self.guest('[ "$(uci -q get dhcp.lan.ignore)" != 1 ]')
        self.assertEqual((self.root / 'etc/config/firewall').read_bytes(), firewall)
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

    def test_network_and_system_are_saved_without_wireless_sections(self):
        self.boot_defaults()
        self.guest('''
            set -e
            [ "$(uci get network.lan.device)" = br-lan ]
            [ "$(uci get network.lan.ipaddr)" = 192.168.2.1/24 ]
            [ "$(uci get network.br_lan.bridge_empty)" = 1 ]
            ! uci -q get network.br_lan.ports
            [ "$(uci get network.wan.device)" = eth0 ]
            [ "$(uci get network.wan.proto)" = dhcp ]
            [ "$(uci get network.wan.hostname)" = '*' ]
            [ "$(uci get network.wan6.noclientfqdn)" = 1 ]
            [ "$(uci get dhcp.lan.dhcpv4)" = server ]
            [ "$(uci get dhcp.lan.dhcpv6)" = server ]
            [ "$(uci get dhcp.lan.ra)" = server ]
            [ "$(uci get system.@system[0].hostname)" = Zero ]
            [ "$(uci get system.@system[0].zonename)" = Asia/Singapore ]
            [ "$(uci get system.@system[0].timezone)" = '<+08>-8' ]
        ''')

    def test_preinit_never_configures_wired_recovery(self):
        self.guest('''
            set -e
            boot_hook_add() { :; }
            . /lib/preinit/00_preinit.conf
            . /lib/preinit/10_indicate_preinit
            . /lib/preinit/40_run_failsafe_hook
            [ "$pi_preinit_no_failsafe" = y ]
            [ -z "$pi_ifname" ]
            # Fail on any network mutation or failsafe entry, without touching
            # host interfaces. Exercise the official functions themselves.
            ip() { echo "Unexpected preinit ip: $*" >&2; exit 1; }
            netmsg() { echo 'Unexpected preinit network message' >&2; exit 1; }
            lock() { echo 'Unexpected failsafe lock' >&2; exit 1; }
            boot_run_hook() { echo 'Unexpected failsafe hook' >&2; exit 1; }
            for ifname in '' eth0; do
                preinit_ip
                [ -z "$pi_ifname" ]
            done
            FAILSAFE=true
            run_failsafe_hook
            [ "$(uci get network.wan.device)" = eth0 ]
            ! uci -q get network.lan
        ''')


if __name__ == '__main__':
    unittest.main()
