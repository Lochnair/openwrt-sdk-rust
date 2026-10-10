ARG SAFE_TARGET
ARG VERSION

FROM ghcr.io/openwrt/sdk:${SAFE_TARGET}-${VERSION} AS tooling

# The SDK runs on amd64 even when compiling for another OpenWrt target.
USER root
RUN curl -fsSL https://github.com/mozilla/sccache/releases/download/v0.18.0/sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz -o /tmp/sccache.tar.gz && \
    echo '45f1447fbe231e3037bde351ef70677dd212216c8d62ae7ca409fecc4d6acc89  /tmp/sccache.tar.gz' | sha256sum -c - && \
    tar -xzf /tmp/sccache.tar.gz -C /tmp && \
    install -m 0755 /tmp/sccache-v0.18.0-x86_64-unknown-linux-musl/sccache /usr/local/bin/sccache && \
    rm -rf /tmp/sccache.tar.gz /tmp/sccache-v0.18.0-x86_64-unknown-linux-musl
USER buildbot

FROM tooling AS configured
ARG SAFE_TARGET
ARG VERSION
ENV SCCACHE_REDIS_KEY_PREFIX=openwrt-rust:${VERSION}: \
    CARGO_INCREMENTAL=0
COPY --chmod=0755 ci/build-rust.sh /usr/local/bin/build-rust
RUN ./scripts/feeds update packages && \
    ./scripts/feeds install rust && \
    printf '\nCONFIG_RUST_SCCACHE=y\n' >> .config && \
    make defconfig && \
    grep -qx 'CONFIG_RUST_SCCACHE=y' .config && \
    sed -i 's/--set=llvm.download-ci-llvm=true/--set=llvm.download-ci-llvm=false/g' feeds/packages/lang/rust/Makefile && \
    sed -i '/--set=llvm.download-ci-llvm=false/a\	--set=build.ccache=sccache \\' feeds/packages/lang/rust/Makefile && \
    sed -i '/^define Host\/Compile/i RUST_BUILD_JOBS ?= 8' feeds/packages/lang/rust/Makefile && \
    sed -i 's/--build-dir /--jobs=$(RUST_BUILD_JOBS) --build-dir /' feeds/packages/lang/rust/Makefile

FROM configured AS sdk
ARG JOBS=8
# A non-secret endpoint on the runner; do not persist it in the published image.
ARG SCCACHE_REDIS_ENDPOINT
RUN build-rust
