#!/usr/bin/env python3
"""Offline checks for release-specific SDK selection and registry pagination."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import matrix


class MatrixTests(unittest.TestCase):
    versions = {"stable_version": "25.12.5", "oldstable_version": "24.10.8"}

    def resolve(self, targets):
        releases = {
            "25.12.5": {"x86/64": "x86_64", "siflower/sf21": "riscv64", "ixp4xx/generic": "armeb"},
            "24.10.8": {"x86/64": "x86_64", "malta/be64": "mips64", "arc/generic": "arc"},
        }
        with patch.object(matrix, "release_targets", side_effect=lambda v, tags: releases[v]):
            with contextlib.redirect_stderr(io.StringIO()):
                return matrix.resolve(self.versions, targets, set())["include"]

    def test_all_uses_each_releases_available_supported_sdks(self):
        self.assertEqual(self.resolve("all"), [
            {"target": "x86-64", "version": "25.12.5"},
            {"target": "siflower-sf21", "version": "25.12.5"},
            {"target": "x86-64", "version": "24.10.8"},
            {"target": "malta-be64", "version": "24.10.8"},
        ])

    def test_explicit_targets_deduplicate_and_skip_unavailable_combinations(self):
        self.assertEqual(self.resolve("siflower/sf21,x86/64,x86/64,ixp4xx/generic"), [
            {"target": "siflower-sf21", "version": "25.12.5"},
            {"target": "x86-64", "version": "25.12.5"},
            {"target": "x86-64", "version": "24.10.8"},
        ])

    def test_invalid_or_empty_selection_fails(self):
        for targets in ("", "../bad", "x86/64;echo", "all,x86/64", "ixp4xx/generic", "missing/generic"):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                self.resolve(targets)
        for versions in ({}, {"stable_version": "../bad"}):
            with self.assertRaises(ValueError):
                matrix.resolve(versions, "all", set())

    def test_discovery_excludes_aliases_and_other_releases_and_keeps_sdk_only_targets(self):
        base = "https://downloads.openwrt.org/releases/25.12.5"
        source = "https://raw.githubusercontent.com/openwrt/openwrt/v25.12.5/target/linux"
        responses = {
            f"{base}/targets/": '<a href="x86/">x86</a><a href="ixp4xx/">ixp4xx</a>'
                                 '<a href="malta/">malta</a><a href="ramips/">ramips</a>',
            f"{base}/.targets.json": json.dumps({"x86/64": "x86_64", "ixp4xx/generic": "armeb_xscale"}),
            f"{source}/malta/be64/target.mk": "ARCH:=mips64\n",
            f"{source}/ramips/rt288x/target.mk": "SUBTARGET:=rt288x\n",
            f"{source}/ramips/Makefile": "ARCH:=mipsel\n",
        }
        tags = {"x86-64-25.12.5", "ixp4xx-generic-25.12.5", "malta-be64-25.12.5",
                "ramips-rt288x-25.12.5", "x86_64-25.12.5", "arm_cortex-a7-25.12.5",
                "x86-generic-24.10.8", "x86-64-v25.12.5"}
        with patch.object(matrix, "fetch", side_effect=lambda url: responses[url]):
            self.assertEqual(matrix.release_targets("25.12.5", tags), {
                "ixp4xx/generic": "armeb", "malta/be64": "mips64",
                "ramips/rt288x": "mipsel", "x86/64": "x86_64",
            })

    def test_missing_images_or_unknown_architecture_fails(self):
        with patch.object(matrix, "fetch", return_value='<a href="malta/">malta</a>'):
            with self.assertRaisesRegex(ValueError, "No SDK images"):
                matrix.release_targets("25.12.5", set())
        def response(url):
            if url.endswith("/targets/"):
                return '<a href="malta/">malta</a>'
            return "{}" if url.endswith(".json") else "SUBTARGET:=be\n"
        with patch.object(matrix, "fetch", side_effect=response):
            with self.assertRaisesRegex(ValueError, "Cannot determine architecture"):
                matrix.release_targets("25.12.5", {"malta-be-25.12.5"})

    def test_registry_pagination(self):
        first = io.BytesIO(b'{"tags":["x86-64-25.12.5"]}')
        first.headers = {"Link": '</v2/openwrt/sdk/tags/list?last=x86-64-25.12.5&n=1000>; rel="next"'}
        second = io.BytesIO(b'{"tags":["malta-be-24.10.8"]}')
        second.headers = {}
        with patch.object(matrix, "fetch", return_value='{"token":"public-pull-token"}'):
            with patch.object(matrix.urllib.request, "urlopen", side_effect=[first, second]) as opened:
                self.assertEqual(matrix.sdk_tags(), {"x86-64-25.12.5", "malta-be-24.10.8"})
        self.assertEqual(opened.call_args_list[1].args[0].full_url,
                         "https://ghcr.io/v2/openwrt/sdk/tags/list?last=x86-64-25.12.5&n=1000")


if __name__ == "__main__":
    unittest.main()
