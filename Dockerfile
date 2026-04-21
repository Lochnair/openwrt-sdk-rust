ARG SAFE_TARGET
ARG VERSION

FROM ghcr.io/openwrt/sdk:${SAFE_TARGET}-${VERSION}

RUN ./scripts/feeds update packages && \
    ./scripts/feeds install rust && \
    make defconfig && \
    sed -i 's/--set=llvm.download-ci-llvm=true/--set=llvm.download-ci-llvm=false/g' feeds/packages/lang/rust/Makefile; \
    { make package/rust/host/compile -j$(nproc) || \
      make package/rust/host/compile -j1 V=sc; } && \
    rm -rf build_dir/host/rustc-*/rustc-*/build \
           dl/rustc-*.tar.*
