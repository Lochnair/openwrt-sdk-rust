#!/usr/bin/env python3
"""Offline checks for release-specific SDK selection and registry pagination."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

import matrix
import registry


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


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.candidates = {"include": [{"target": "x86-64", "version": "25.12.5"}]}
        self.upstream, self.source, self.destination = (Mock(spec=registry.Registry) for _ in range(3))
        self.upstream.digest.return_value = "base"
        self.source.digest.return_value = "hub"
        self.source.labels.return_value = {registry.BASE_LABEL: "base", registry.RECIPE_LABEL: "recipe"}
        self.destination.digest.return_value = "hub"

    def plan(self, force=False):
        with patch.object(matrix, "recipe_fingerprint", return_value="recipe"):
            with contextlib.redirect_stderr(io.StringIO()):
                return matrix.plan(self.candidates, force, self.upstream, self.source, self.destination)

    def test_current_images_produce_empty_matrices(self):
        self.assertEqual(self.plan(), {"build_matrix": {"include": []}, "sync_matrix": {"include": []}})

    def test_legacy_images_synchronize_without_rebuilding(self):
        self.source.labels.return_value = {}
        self.destination.digest.return_value = None
        result = self.plan()
        self.assertEqual(result["build_matrix"]["include"], [])
        self.assertEqual(result["sync_matrix"]["include"], [{"target": "x86-64", "version": "25.12.5", "hub_digest": "hub"}])

    def test_missing_outdated_or_forced_images_build(self):
        for source_digest, labels, force in [
            (None, {}, False),
            ("hub", {registry.BASE_LABEL: "old", registry.RECIPE_LABEL: "recipe"}, False),
            ("hub", {registry.BASE_LABEL: "base", registry.RECIPE_LABEL: "old"}, False),
            ("hub", {}, True),
        ]:
            with self.subTest(source_digest=source_digest, labels=labels, force=force):
                self.source.digest.return_value = source_digest
                self.source.labels.return_value = labels
                result = self.plan(force)
                self.assertEqual(result["sync_matrix"]["include"], [])
                self.assertEqual(result["build_matrix"]["include"], [
                    {"target": "x86-64", "version": "25.12.5", "base_digest": "base", "recipe": "recipe"},
                ])

    def test_ghcr_missing_or_outdated_only_synchronizes(self):
        for digest in (None, "old"):
            with self.subTest(digest=digest):
                self.destination.digest.return_value = digest
                result = self.plan()
                self.assertEqual(len(result["sync_matrix"]["include"]), 1)
                self.assertEqual(result["build_matrix"]["include"], [])

    def test_registry_errors_abort_planning(self):
        for image in (self.upstream, self.source, self.destination):
            with self.subTest(image=image):
                image.digest.side_effect = RuntimeError("Registry unavailable")
                with self.assertRaisesRegex(RuntimeError, "Registry unavailable"):
                    self.plan()
                image.digest.side_effect = None

    def test_cli_writes_empty_plan_guards_and_force_build_outputs(self):
        def response(request, **kwargs):
            url = request.full_url if hasattr(request, "full_url") else request
            if url.endswith(".versions.json"):
                data = MatrixTests.versions
            elif "/token?" in url:
                data = {"token": "fixture"}
            elif "/tags/list?" in url:
                data = {"tags": ["x86-64-25.12.5", "x86-64-24.10.8"]}
            elif url.endswith("/targets/"):
                data = '<a href="x86/">x86</a>'
            else:
                data = {"x86/64": "x86_64"}
            body = data if isinstance(data, str) else json.dumps(data)
            result = io.BytesIO(body.encode())
            result.headers = {}
            return result

        self.source.labels.return_value = {}
        with tempfile.TemporaryDirectory() as tmp:
            for force in ("false", "true"):
                output = Path(tmp) / force
                with patch.dict(os.environ, {"TARGETS": "all", "FORCE_REBUILD": force, "GITHUB_OUTPUT": str(output)}):
                    with patch.object(matrix.urllib.request, "urlopen", side_effect=response), \
                         patch.object(registry, "Registry", return_value=self.upstream), \
                         patch.object(registry, "dockerhub", return_value=self.source), \
                         patch.object(registry, "ghcr", return_value=self.destination), \
                         contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        runpy.run_path(matrix.__file__, run_name="__main__")
                outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
                self.assertEqual(outputs["has_builds"], force)
                self.assertEqual(outputs["has_syncs"], "false")
                self.assertEqual(json.loads(outputs["sync_matrix"]), {"include": []})
                self.assertEqual(len(json.loads(outputs["build_matrix"])["include"]), 2 if force == "true" else 0)


if __name__ == "__main__":
    unittest.main()
