# Run from /builder in a configured malta-le64 SDK; see VALIDATION.md.
TOPDIR:=$(CURDIR)
include $(TOPDIR)/rules.mk
include $(TOPDIR)/feeds/packages/lang/rust/rust-values.mk

.PHONY: check-mips64el check-mips64el-config
check-mips64el: check-mips64el-config
	@$(MAKE) --no-print-directory -sf $(firstword $(MAKEFILE_LIST)) check-mips64el-config \
		REAL_GNU_TARGET_NAME=mips64el-openwrt-linux-muslabi64

check-mips64el-config:
	@test '$(ARCH)' = mips64el
	@test '$(CONFIG_USE_MUSL)' = y
	@test '$(CONFIG_SOFT_FLOAT)' = y
	@test '$(RUSTC_TARGET_ARCH)' = mips64el-unknown-linux-muslabi64
	@case '$(CARGO_RUSTFLAGS)' in *-Ctarget-feature=+soft-float*) ;; *) exit 1 ;; esac
	@test "$$CARGO_TARGET_MIPS64EL_UNKNOWN_LINUX_MUSLABI64_RUSTFLAGS" = '-Ctarget-feature=+soft-float'
	@echo 'Malta Rust target, package flags and bootstrap environment: OK'
