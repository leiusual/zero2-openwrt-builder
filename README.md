# NanoPi Zero2 OpenWrt

基于官方 **OpenWrt v25.12.6** 为 NanoPi Zero2 构建 SD 卡镜像。
截至 2026-10-30，这是[官方最新稳定版本](https://openwrt.org/releases/25.12/start)。
[sources.env](sources.env) 固定 tag 和提交；feeds 沿用该 release 自带的固定版本。

在官方源码上直接加入三份补丁，校验值见 [patches/SHA256SUMS](patches/SHA256SUMS)：

1. **板级适配**：回移 Linux 的 Zero2 DTS 和 OpenWrt LED 别名，添加标准镜像定义及 `02_network` 板型分支，唯一的 `eth0` 作为 DHCP WAN；声明不带有线设备或端口的静态 LAN，供官方脚本启用 LAN DHCP 服务。DTS 回移来源为 [OpenWrt PR #22707](https://github.com/openwrt/openwrt/pull/22707) 的固定提交；镜像直接指定 Zero2 专用 U-Boot。
2. **专用 U-Boot**：回移上游 [`ebf46b588f43`](https://github.com/u-boot/u-boot/commit/ebf46b588f43ffcff7b92b341a7a567a705a8c60) 的 Zero2 defconfig、板级 DTS 和供电初始化设置，接入 OpenWrt `uboot-rockchip`。沿用官方 U-Boot 2025.10，以及 rkbin 的 `rk3528_ddr_1056MHz_v1.11.bin` 和 `rk3528_bl31_v1.20.elf`。旧版 U-Boot 公共 DTS 尚无 GPU 节点，因此只在 U-Boot DTS 中省去该引用。
3. **启动修复**：保留 [PR #22706](https://github.com/openwrt/openwrt/pull/22706) 的电源域 workaround，合并必要的注册错误处理。idle-only 域保持开启；允许其他域在个别域延迟探测时注册；检查 `pm_genpd_init()` 返回值，全部延迟时重试。

启动修复仍有已知边界：控制器注册成功后，被跳过的域不会补注册，GPU 等功能可能不可用。
它是已验证启动组合所需的 workaround，并非完整的上游电源管理修复。

Wi-Fi 使用官方 OpenWrt 的 `kmod-rtw88-8822ce`、`rtl8822ce-firmware` 和 `wpad-basic-mbedtls`。
软件选择见 [openwrt.config](openwrt.config)，包括英文 LuCI HTTPS、WireGuard 及 LuCI 协议支持、dig。

默认设置参考[官方 Firmware Selector](https://firmware-selector.openwrt.org/?version=25.12.6) 的首次启动脚本，
使用[官方 UCI defaults 机制](https://openwrt.org/docs/guide-developer/uci-defaults)，
直接安装 [99-zero2-defaults](files/etc/uci-defaults/99-zero2-defaults)，增量配置：

| 项目 | 默认值 |
| --- | --- |
| 主机名 | `Zero` |
| 时区 | `Asia/Singapore`，UTC+8 |
| WAN | `eth0`，DHCP；沿用设备自身 MAC |
| WAN 主机名上报 | IPv4 `hostname='*'`；IPv6 `noclientfqdn='1'` |
| LAN | `br-lan`，无有线端口，`bridge_empty='1'`；`192.168.2.1/24` |
| Wi-Fi | 开启 5 GHz AP，SSID `Zero`，国家 `CN`，开放网络，接入 LAN |
| 无线硬件参数 | 路径、信道和带宽由官方硬件检测生成；定制脚本不指定信道 |
| DHCP / 防火墙 | 沿用官方 LAN / WAN 默认策略 |
| root 密码 | 沿用官方初始空密码 |

采用全新刷写或不保留配置升级，脚本只覆盖与官方生成结果不同的设置。
WAN/WAN6 的 `eth0`、DHCP/DHCPv6 沿用板级生成结果；Wi-Fi 的 5 GHz、AP 模式、接入 LAN 和无加密沿用官方无线检测结果。
定制脚本只设置系统名称和时区、创建空桥 LAN、关闭 WAN 主机名上报，以及设置 Wi-Fi 国家、名称和接口启用。
LAN 由脚本创建，因此保留 `ip6assign=60`，以沿用官方静态 LAN 的 IPv6 前缀分配方式。
脚本直接写入固定值，不检测硬件运行状态，不使用初始化完成标记，也不处理旧配置迁移。
系统和网络先保存，无线配置写入失败不会跳过前面的设置；日志位于 `/tmp/zero2-setup.log`。
脚本由官方 `boot` 服务执行，成功后删除，UCI 写入失败则按官方机制保留至下次启动。
使用官方 `CONFIG_TARGET_PREINIT_DISABLE_FAILSAFE=y` 关闭 OpenWrt failsafe，且不指定 preinit 网口。
启动早期不会给 `eth0` 设置临时恢复地址；正常启动后它只作为 DHCP WAN。启动失败也不会将其切换为 LAN，恢复可重新写入 SD 卡。
没有自定义 preinit 钩子，也不从 `board.d` 动态复制脚本。

在 GitHub **Actions → Build Zero2 → Run workflow** 手动构建。
每次从全新源码目录开始，下载 `zero2-sd-image` 中的 `*.img.gz` 后整卡写入 SD 卡。
连接 Wi-Fi `Zero`，访问 `https://192.168.2.1`。
整卡写入会包含 U-Boot；分区布局相同的常规 sysupgrade 通常只更新分区，不更新分区前的引导区。

构建会验证包清单、镜像 SHA256、LBA 64 的 Zero2 U-Boot 内容，并在镜像根文件系统中运行真实 UCI 初始化回归检查。
检查执行镜像自带的完整 `uci-defaults` 序列，覆盖全新初始化的 LAN DHCP 服务、空桥、WAN 主机名参数、Wi-Fi 默认值，以及启动期间不启用有线恢复网络。
测试提供模拟的无线硬件能力，由官方 `mac80211.uc` 生成无线配置；硬件启动和无线连接仍需实机验收。
`zero2-build-logs` 保存本次源码差异、配置、构建及验证日志；工作流只上传 Artifacts。

本地安装 OpenWrt 构建依赖后执行：

```sh
bash scripts/prepare.sh
cd work/openwrt
make download -j8
make -j"$(nproc)"
```

`scripts/prepare.sh` 要求 `work/openwrt` 不存在。镜像验证另需 `qemu-user-static`、`proot`、`squashfs-tools`，
使用 `python3 scripts/check_image.py work/openwrt <镜像路径>`。
