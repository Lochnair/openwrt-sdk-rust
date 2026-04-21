ARG SAFE_TARGET
ARG VERSION

FROM ghcr.io/openwrt/sdk:${SAFE_TARGET}-${VERSION}

RUN ./scripts/feeds update packages && \
    make defconfig && \
    { make package/feeds/packages/rust/host-compile -j$(nproc) || \
      make package/feeds/packages/rust/host-compile -j1 V=sc; } && \
    rm -rf build_dir/host/rustc-*/rustc-*/build \
           dl/rustc-*.tar.*
