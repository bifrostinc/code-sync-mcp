#!/bin/bash
set -e

WORK_DIR=$(pwd)
rm -rf toolchain rsync
mkdir -p toolchain

echo "I: Downloading prebuilt toolchain"
wget --continue https://skarnet.org/toolchains/native/x86_64-linux-musl_pc-11.3.0.tar.xz -O /tmp/x86_64-linux-musl_pc.tar.xz || echo "Failed to find x86_64-linux-musl_pc-11.3.0.tar.xz. Please open your browser at https://skarnet.org/toolchains/native and find the correct file to fix this"
tar -xf /tmp/x86_64-linux-musl_pc.tar.xz -C toolchain

# Full absolute path to toolchain
TOOLCHAIN_PATH="$WORK_DIR/toolchain/x86_64-linux-musl_pc-11.3.0"
echo "Using toolchain at: $TOOLCHAIN_PATH"

# Export environment variables with absolute paths
export PATH="$TOOLCHAIN_PATH/bin:$PATH"
export CC="$TOOLCHAIN_PATH/bin/gcc"
export LD="$TOOLCHAIN_PATH/bin/ld"
export LDFLAGS="-static"
export CFLAGS="-static"

echo "Getting rsync source"
git clone https://github.com/WayneD/rsync.git

echo "Building rsync"
cd rsync/
./configure --host="x86_64-linux-musl" \
            --disable-openssl \
            --disable-xxhash \
            --disable-zstd \
            --disable-lz4 \
            --disable-md2man \
            --disable-md5-asm \
            --disable-roll-simd
make
strip rsync

echo "Build complete."

cp rsync /output/rsync-linux-amd64