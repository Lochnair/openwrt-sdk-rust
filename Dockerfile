ARG SAFE_TARGET
ARG VERSION

FROM ghcr.io/openwrt/sdk:${SAFE_TARGET}-${VERSION}

RUN ./scripts/feeds update packages && \
    make package/feeds/packages/rust/host-compile -j$(nproc) && \
    rm -rf build_dir/host/rustc-*/rustc-*/build \
           dl/rustc-*.tar.*
