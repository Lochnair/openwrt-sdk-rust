#!/usr/bin/env python3
"""Registry protocol and digest-preserving synchronization regression checks."""
import base64
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

import registry


def encoded(data):
    raw = json.dumps(data, separators=(",", ":")).encode()
    return raw, "sha256:" + hashlib.sha256(raw).hexdigest()


class RegistryTests(unittest.TestCase):
    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.requests.append((self.path, self.headers.get("Authorization")))
                status, headers, body = self.server.responses.get(self.path, (
                    404, {}, b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}'))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.responses = {}
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.image = registry.Registry(f"http://127.0.0.1:{self.server.server_port}", "owner/sdk")

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def manifest(self, reference="tag", labels=None, index=False):
        config, config_digest = encoded({"config": {"Labels": labels}})
        self.server.responses[f"/v2/owner/sdk/blobs/{config_digest}"] = (200, {}, config)
        manifest, digest = encoded({"schemaVersion": 2, "config": {"digest": config_digest}})
        self.server.responses[f"/v2/owner/sdk/manifests/{digest}"] = (200, {}, manifest)
        if index:
            manifest, digest = encoded({"schemaVersion": 2, "manifests": [
                {"digest": digest, "platform": {"os": "linux", "architecture": "amd64"}},
                {"digest": "sha256:" + "0" * 64, "platform": {"os": "unknown", "architecture": "unknown"}},
            ]})
            self.server.responses[f"/v2/owner/sdk/manifests/{digest}"] = (200, {}, manifest)
        self.server.responses[f"/v2/owner/sdk/manifests/{reference}"] = (
            200, {"Docker-Content-Digest": digest}, manifest)
        return digest

    def test_missing_is_only_a_structured_registry_404(self):
        self.assertIsNone(self.image.digest("missing", missing=True))
        for status, body in [
            (403, b'{"errors":[{"code":"DENIED"}]}'),
            (429, b'{"errors":[{"code":"TOOMANYREQUESTS"}]}'),
            (500, b'{"errors":[{"code":"UNKNOWN"}]}'),
            (404, b'proxy error'),
            (404, b'{"errors":[{"code":"DENIED"}]}'),
        ]:
            with self.subTest(status=status, body=body):
                self.server.responses["/v2/owner/sdk/manifests/tag"] = (status, {}, body)
                with self.assertRaisesRegex(RuntimeError, "Registry error"):
                    self.image.digest("tag", missing=True)

    def test_bearer_authentication_and_auth_errors(self):
        path = "/v2/owner/sdk/manifests/tag"
        challenge = f'Bearer realm="{self.image.base}/token",service="test",scope="repository:owner/sdk:pull"'
        self.server.responses[path] = (401, {"WWW-Authenticate": challenge}, b'{}')
        self.server.responses["/token?service=test&scope=repository%3Aowner%2Fsdk%3Apull"] = (
            200, {}, b'{"token":"fixture-token"}')
        self.image.username, self.image.password = "owner", "secret"
        # The rejected bearer retry must fail rather than being treated as absent.
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            self.image.digest("tag", missing=True)
        auth = self.server.requests[1][1]
        self.assertEqual(auth, "Basic " + base64.b64encode(b"owner:secret").decode())
        self.assertEqual(self.server.requests[2][1], "Bearer fixture-token")

    def test_labels_from_platform_manifest_keep_the_index_digest(self):
        digest = self.manifest(labels={registry.BASE_LABEL: "base", registry.RECIPE_LABEL: "recipe"}, index=True)
        self.assertEqual(self.image.digest("tag"), digest)
        self.assertEqual(self.image.labels(digest), {registry.BASE_LABEL: "base", registry.RECIPE_LABEL: "recipe"})

    def test_manifest_and_config_corruption_fail(self):
        digest = self.manifest()
        path = "/v2/owner/sdk/manifests/tag"
        status, _, body = self.server.responses[path]
        self.server.responses[path] = (status, {"Docker-Content-Digest": "sha256:" + "0" * 64}, body)
        with self.assertRaisesRegex(RuntimeError, "Manifest digest mismatch"):
            self.image.digest("tag")
        config_path = next(p for p in self.server.responses if "/blobs/" in p)
        self.server.responses[config_path] = (200, {}, b'{}')
        with self.assertRaisesRegex(RuntimeError, "Config digest mismatch"):
            self.image.labels(digest)


class SynchronizationTests(unittest.TestCase):
    def synchronize(self, source, destination, expected=None):
        with contextlib.redirect_stdout(io.StringIO()), patch.dict(os.environ, {"REGISTRY_AUTH_FILE": "/tmp/fixture-auth.json"}):
            registry.synchronize("x86-64-25.12.5", source, destination, expected)

    def images(self, destination_digest=None):
        source, destination = Mock(spec=registry.Registry), Mock(spec=registry.Registry)
        source.image, destination.image = "docker.io/owner/sdk", "ghcr.io/owner/sdk"
        source.digest.return_value = "sha256:" + "1" * 64
        destination.digest.side_effect = [destination_digest, source.digest.return_value]
        return source, destination

    def test_copy_missing_or_outdated_ghcr_by_immutable_digest(self):
        for current in (None, "sha256:" + "2" * 64):
            with self.subTest(current=current):
                source, destination = self.images(current)
                with patch.object(registry.subprocess, "run") as copied:
                    self.synchronize(source, destination, source.digest.return_value)
                self.assertEqual(copied.call_args.args[0], [
                    "skopeo", "copy", "--all", "--preserve-digests", "--authfile", "/tmp/fixture-auth.json",
                    f"docker://docker.io/owner/sdk@{source.digest.return_value}",
                    "docker://ghcr.io/owner/sdk:x86-64-25.12.5",
                ])

    def test_identical_registries_do_not_copy(self):
        source, destination = self.images("sha256:" + "1" * 64)
        with patch.object(registry.subprocess, "run") as copied:
            self.synchronize(source, destination)
        copied.assert_not_called()

    def test_missing_or_changed_source_fails_without_copy(self):
        for digest in (None, "sha256:" + "2" * 64):
            source, destination = self.images()
            source.digest.return_value = digest
            with patch.object(registry.subprocess, "run") as copied:
                with self.assertRaises(RuntimeError):
                    self.synchronize(source, destination, "sha256:" + "1" * 64)
            copied.assert_not_called()

    def test_copy_failures_and_post_copy_mismatches_fail(self):
        source, destination = self.images()
        with patch.object(registry.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "skopeo")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.synchronize(source, destination)
        source, destination = self.images()
        destination.digest.side_effect = [None, "sha256:" + "2" * 64]
        with patch.object(registry.subprocess, "run"):
            with self.assertRaisesRegex(RuntimeError, "digest verification failed"):
                self.synchronize(source, destination)
        source, destination = self.images()
        source.digest.side_effect = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]
        with patch.object(registry.subprocess, "run"):
            with self.assertRaisesRegex(RuntimeError, "digest verification failed"):
                self.synchronize(source, destination)


if __name__ == "__main__":
    unittest.main()
