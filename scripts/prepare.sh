#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
source "$root/sources.env"
tree="$root/work/openwrt"
logs="$root/artifacts/logs"
[[ ! -e "$tree" ]] || { echo 'work/openwrt must be absent for a clean build.' >&2; exit 1; }
mkdir -p "$tree" "$logs"
(
    cd "$root/patches"
    sha256sum -c SHA256SUMS
)
sh -n "$root/files/etc/uci-defaults/99-zero2-defaults"
test ! -x "$root/files/etc/uci-defaults/99-zero2-defaults"

git -C "$tree" init -q
git -C "$tree" fetch --depth=1 https://github.com/openwrt/openwrt.git "refs/tags/$OPENWRT_TAG"
test "$(git -C "$tree" rev-parse 'FETCH_HEAD^{commit}')" = "$OPENWRT_COMMIT"
git -C "$tree" checkout --detach "$OPENWRT_COMMIT"
for patch in "$root"/patches/*.patch; do
    git -C "$tree" apply --check "$patch"
    git -C "$tree" apply "$patch"
done
git -C "$tree" add -N package/boot/uboot-rockchip target/linux/rockchip
git -C "$tree" diff --binary > "$logs/source-changes.patch"
cp -a "$root/files" "$tree/"
cp "$root/sources.env" "$tree/feeds.conf.default" "$logs/"

cd "$tree"
./scripts/feeds update -a
./scripts/feeds install luci-ssl luci-proto-wireguard bind-dig
cp "$root/openwrt.config" .config
make defconfig

while IFS= read -r setting; do
    case "$setting" in CONFIG_*=*) grep -Fxq "$setting" .config ;; esac
done < "$root/openwrt.config"
grep -Fxq 'CONFIG_PACKAGE_u-boot-nanopi-zero2-rk3528=y' .config
if grep -Eq '^CONFIG_PACKAGE_u-boot-radxa-e20c-rk3528=[ym]$' .config; then
    echo 'Unexpected E20C bootloader selected' >&2
    exit 1
fi
if grep -Eq '^CONFIG_PACKAGE_luci-i18n-.*=y$' .config; then
    echo 'Unexpected LuCI translation package' >&2
    exit 1
fi
cp .config "$logs/openwrt.config"
./scripts/diffconfig.sh > "$logs/openwrt.diffconfig"
