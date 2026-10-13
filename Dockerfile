# syntax=docker/dockerfile:1
ARG SAFE_TARGET
ARG VERSION
ARG SDK_IMAGE=ghcr.io/openwrt/sdk:${SAFE_TARGET}-${VERSION}

FROM ${SDK_IMAGE} AS tooling

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
    SCCACHE_S3_KEY_PREFIX=openwrt-rust/${VERSION}/ \
    SCCACHE_BUCKET=sccache \
    SCCACHE_REGION=us-east-1 \
    SCCACHE_S3_USE_SSL=false \
    SCCACHE_MULTILEVEL_CHAIN=redis,s3 \
    SCCACHE_MULTILEVEL_WRITE_ERROR_POLICY=all \
    CARGO_INCREMENTAL=0
COPY --chmod=0755 ci/build-rust.sh /usr/local/bin/build-rust
COPY --chmod=0755 ci/sccache-stats.py /usr/local/bin/sccache-stats
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
# Non-secret runner endpoints apply during the build, not as runtime defaults.
ARG SCCACHE_REDIS_ENDPOINT
ARG SCCACHE_ENDPOINT
RUN --mount=type=secret,id=aws-access-key-id,env=AWS_ACCESS_KEY_ID,required=true \
    --mount=type=secret,id=aws-secret-access-key,env=AWS_SECRET_ACCESS_KEY,required=true \
    build-rust
