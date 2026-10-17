"""Offline tests; run with unittest discover -s actions/openwrt-matrix."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import matrix


class MatrixTests(unittest.TestCase):
    metadata = {"stable_version": "25.12.5", "oldstable_version": "24.10.8"}
    dump = "x86_64 x86/64\narm_cortex-a7_neon-vfpv4 zboard/first aboard/second\n"

    def test_parsing_preserves_upstream_target_order(self):
        self.assertEqual(matrix.parse_architectures("\n" + self.dump), {
            "x86_64": ["x86/64"],
            "arm_cortex-a7_neon-vfpv4": ["zboard/first", "aboard/second"],
        })

    def test_invalid_and_empty_upstream_output(self):
        for output in ("", " \n", "arch", "arch invalid", "arch board/sub/extra",
                       "arch a/b\narch c/d", "arch a/b a/b", "arch a/b\nother a/b",
                       "warning: metadata incomplete"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                matrix.parse_architectures(output)

    def test_aliases_explicit_versions_rc_and_deduplication(self):
        self.assertEqual(matrix.resolve_versions(
            "stable,24.10.8,oldstable,25.12.5,23.05.0-rc4", self.metadata),
            ["25.12.5", "24.10.8", "23.05.0-rc4"])
        self.assertEqual(matrix.resolve_versions("stable,oldstable", {
            "stable_version": "24.10.8", "oldstable_version": "24.10.8"}), ["24.10.8"])

    def test_invalid_versions_and_missing_aliases(self):
        for requested in ("", "stable,", "snapshot", "v24.10.8", "../bad", "24.10", "all"):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                matrix.resolve_versions(requested, self.metadata)
        for metadata in ({}, {"stable_version": ""}, {"stable_version": 123}):
            with self.assertRaises(ValueError):
                matrix.resolve_versions("stable", metadata)

    def rows(self, arches="all", targets="all"):
        return matrix.select_rows("24.10.8", matrix.parse_architectures(self.dump),
                                  matrix.selection(arches, matrix.ARCH),
                                  matrix.selection(targets, matrix.TARGET))

    def test_all_is_sorted_and_one_row_per_arch(self):
        self.assertEqual(self.rows(), [
            {"version": "24.10.8", "package_arch": "arm_cortex-a7_neon-vfpv4", "target": "zboard/first"},
            {"version": "24.10.8", "package_arch": "x86_64", "target": "x86/64"},
        ])

    def test_filters_and_duplicate_selections_keep_first_representative(self):
        self.assertEqual(self.rows(targets="aboard/second,zboard/first,aboard/second"), self.rows()[:1])
        self.assertEqual(self.rows(arches="x86_64,x86_64"), self.rows()[1:])
        self.assertEqual(self.rows(arches="x86_64", targets="x86/64"), self.rows()[1:])

    def test_missing_selection_and_empty_intersection_fail(self):
        for arches, targets in (("missing", "all"), ("all", "missing/generic"),
                                ("x86_64", "aboard/second"), ("x86_64,missing", "all")):
            with self.subTest(arches=arches, targets=targets), self.assertRaisesRegex(ValueError, "24.10.8"):
                self.rows(arches, targets)

    def test_invalid_filters(self):
        for value in ("", "all,x86_64", "x86_64,", "bad;echo"):
            with self.assertRaises(ValueError):
                matrix.selection(value, matrix.ARCH)
        with self.assertRaises(ValueError):
            matrix.selection("x86-64", matrix.TARGET)

    def test_generate_checks_each_deduplicated_release(self):
        with patch.object(matrix, "fetch_versions", return_value=self.metadata), patch.object(
                matrix, "release_architectures", return_value=matrix.parse_architectures(self.dump)) as release:
            result = matrix.generate("stable,oldstable,24.10.8", "all", "all")
        self.assertEqual([call.args[0] for call in release.call_args_list], ["25.12.5", "24.10.8"])
        self.assertEqual(len(result["include"]), 4)

    def test_unavailable_on_second_release_is_not_skipped(self):
        with patch.object(matrix, "fetch_versions", return_value=self.metadata), patch.object(
                matrix, "release_architectures", side_effect=[{"x86_64": ["x86/64"]}, {"other": ["a/b"]}]):
            with self.assertRaisesRegex(ValueError, "24.10.8.*unavailable"):
                matrix.generate("stable,oldstable", "x86_64", "all")

    def test_fetch_failure_and_malformed_metadata(self):
        with patch.object(matrix.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(urllib.error.URLError):
                matrix.generate("stable", "all", "all")
        for content in (b"[]", b"not json"):
            with patch.object(matrix.urllib.request, "urlopen", return_value=io.BytesIO(content)):
                with self.assertRaises(ValueError):
                    matrix.fetch_versions()

    def test_checkout_and_upstream_command_for_each_release(self):
        def command(args, cwd=None):
            if args[:2] == ["git", "clone"]:
                Path(args[-1]).mkdir()
            return subprocess.CompletedProcess(args, 0, stdout=self.dump, stderr="")
        with tempfile.TemporaryDirectory() as directory, patch.object(matrix, "run", side_effect=command) as run:
            for version in ("24.10.8", "25.12.5"):
                matrix.release_architectures(version, directory)
                checkout = Path(directory) / version
                self.assertTrue((checkout / "tmp").is_dir())
                self.assertEqual(run.call_args.args[0], ["perl", "scripts/dump-target-info.pl", "architectures"])
                self.assertEqual(run.call_args.kwargs["cwd"], checkout)
            clones = [call.args[0] for call in run.call_args_list if call.args[0][:2] == ["git", "clone"]]
            self.assertEqual([args[args.index("--branch") + 1] for args in clones], ["v24.10.8", "v25.12.5"])
            self.assertNotEqual(clones[0][-1], clones[1][-1])

    def test_command_errors_include_diagnostics(self):
        with patch.object(matrix.subprocess, "run", side_effect=subprocess.CalledProcessError(
                1, ["git"], stderr="release tag missing")):
            with self.assertRaisesRegex(ValueError, "release tag missing"):
                matrix.run(["git"])

    def test_upstream_diagnostics_fail_except_known_dump_warning(self):
        warning = '/tmp/release/include/target.mk:287: CPU_TYPE "cortex-a7" doesn\'t correspond to a known type\n'
        for diagnostics in ("make: *** Error 2\n", warning, ""):
            def command(args, cwd=None):
                if args[:2] == ["git", "clone"]:
                    Path(args[-1]).mkdir()
                return subprocess.CompletedProcess(args, 0, stdout=self.dump, stderr=diagnostics)
            with tempfile.TemporaryDirectory() as directory, patch.object(matrix, "run", side_effect=command):
                if diagnostics.startswith("make:"):
                    with self.assertRaisesRegex(ValueError, "metadata diagnostics"):
                        matrix.release_architectures("24.10.8", directory)
                else:
                    self.assertEqual(matrix.release_architectures("24.10.8", directory),
                                     matrix.parse_architectures(self.dump))

    def test_main_emits_compact_output_only_on_success(self):
        result = {"include": self.rows()}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(
                    matrix, "generate", return_value=result), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(matrix.main(), 0)
            self.assertEqual(output.read_text(), "matrix=" + json.dumps(result, separators=(",", ":")) + "\n")
            output.unlink()
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(
                    matrix, "generate", side_effect=ValueError("no metadata")), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(matrix.main(), 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
