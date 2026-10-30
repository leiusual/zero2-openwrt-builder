# NanoPi Zero2 OpenWrt

基于官方 **OpenWrt v25.12.6** 为 NanoPi Zero2 构建 SD 卡镜像。
截至 2026-10-30，这是[官方最新稳定版本](https://openwrt.org/releases/25.12/start)。
[sources.env](sources.env) 固定 tag 和提交；feeds 沿用该 release 自带的固定版本。

在官方源码上直接加入三份补丁，校验值见 [patches/SHA256SUMS](patches/SHA256SUMS)：

1. **板级适配**：回移 Linux 的 Zero2 DTS 和 OpenWrt LED 别名，添加标准镜像定义及 `02_network` 板型分支，唯一的 `eth0` 作为 DHCP WAN。DTS 回移来源为 [OpenWrt PR #22707](https://github.com/openwrt/openwrt/pull/22707) 的固定提交；镜像直接指定 Zero2 专用 U-Boot。
2. **专用 U-Boot**：回移上游 [`ebf46b588f43`](https://github.com/u-boot/u-boot/commit/ebf46b588f43ffcff7b92b341a7a567a705a8c60) 的 Zero2 defconfig、板级 DTS 和供电初始化设置，接入 OpenWrt `uboot-rockchip`。沿用官方 U-Boot 2025.10，以及 rkbin 的 `rk3528_ddr_1056MHz_v1.11.bin` 和 `rk3528_bl31_v1.20.elf`。旧版 U-Boot 公共 DTS 尚无 GPU 节点，因此只在 U-Boot DTS 中省去该引用。
3. **启动修复**：保留 [PR #22706](https://github.com/openwrt/openwrt/pull/22706) 的电源域 workaround，合并必要的注册错误处理。idle-only 域保持开启；允许其他域在个别域延迟探测时注册；检查 `pm_genpd_init()` 返回值，全部延迟时重试。

启动修复仍有已知边界：控制器注册成功后，被跳过的域不会补注册，GPU 等功能可能不可用。
它是已验证启动组合所需的 workaround，并非完整的上游电源管理修复。

Wi-Fi 使用官方 OpenWrt 的 `kmod-rtw88-8822ce`、`rtl8822ce-firmware` 和 `wpad-basic-mbedtls`。
软件选择见 [openwrt.config](openwrt.config)，包括英文 LuCI HTTPS、WireGuard 及 LuCI 协议支持、dig。

默认设置按照[官方 UCI defaults 文档](https://openwrt.org/docs/guide-developer/uci-defaults)，
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

脚本由官方 `boot` 服务执行，成功后删除，失败则保留下次启动重试。
初始化成功后写入 `system.@system[0].zero2_defaults_version=1`；后续保留配置升级会保留用户设置。
首次迁入本项目应使用全新刷写或不保留配置；旧固件没有此标记，保留配置迁入时也会执行一次默认设置。
没有自定义 preinit 钩子，也不从 `board.d` 动态复制脚本。

在 GitHub **Actions → Build Zero2 → Run workflow** 手动构建。
每次从全新源码目录开始，下载 `zero2-sd-image` 中的 `*.img.gz` 后整卡写入 SD 卡。
连接 Wi-Fi `Zero`，访问 `https://192.168.2.1`。
整卡写入会包含 U-Boot；分区布局相同的常规 sysupgrade 通常只更新分区，不更新分区前的引导区。

构建会验证包清单、镜像 SHA256、LBA 64 的 Zero2 U-Boot 内容，并在镜像根文件系统中运行真实 UCI 初始化回归检查。
检查覆盖空桥、WAN 主机名参数、Wi-Fi 默认值、保留配置升级和失败重试。
测试使用模拟的已检测无线设备配置；硬件启动和无线连接仍需实机验收。
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
