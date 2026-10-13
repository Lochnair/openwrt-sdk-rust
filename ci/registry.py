#!/usr/bin/env python3
"""Read registry state and mirror Docker Hub images without changing digests."""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE_LABEL = "org.opencontainers.image.base.digest"
RECIPE_LABEL = "net.lochnair.openwrt-sdk-rust.recipe"
MANIFEST_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


class Registry:
    def __init__(self, host, repository, username="", password=""):
        self.host = host
        self.repository = repository
        self.base = host if "://" in host else f"https://{host}"
        self.username, self.password = username, password
        if bool(username) != bool(password):
            raise ValueError(f"Supply both username and token for {host}")
        self.token = None

    @property
    def image(self):
        host = "docker.io" if self.host == "registry-1.docker.io" else self.host.removeprefix("http://").removeprefix("https://")
        return f"{host}/{self.repository}"

    def authorize(self, challenge):
        if not challenge.lower().startswith("bearer "):
            raise RuntimeError(f"Unsupported authentication challenge from {self.host}")
        fields = dict(re.findall(r'([a-z]+)="([^"]*)"', challenge))
        realm = fields.pop("realm", None)
        if not realm:
            raise RuntimeError(f"Missing authentication realm from {self.host}")
        url = realm + ("&" if "?" in realm else "?") + urllib.parse.urlencode(fields)
        headers = {}
        if self.username:
            encoded = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                data = json.load(response)
        except urllib.error.URLError as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            raise RuntimeError(f"Authentication failed for {self.host}: {error}") from error
        self.token = data.get("token") or data.get("access_token")
        if not self.token:
            raise RuntimeError(f"No registry token returned for {self.host}")

    def get(self, path, missing=False):
        headers = {"Accept": MANIFEST_TYPES}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"{self.base}/v2/{self.repository}/{path}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as error:
            if error.code == 401 and not self.token:
                challenge = error.headers.get("WWW-Authenticate", "")
                error.close()
                self.authorize(challenge)
                return self.get(path, missing)
            try:
                with error:
                    codes = {item["code"] for item in json.load(error)["errors"]}
            except (ValueError, KeyError, TypeError):
                codes = set()
            if missing and error.code == 404 and codes and codes <= {"MANIFEST_UNKNOWN", "NAME_UNKNOWN"}:
                return None
            raise RuntimeError(f"Registry error from {self.host}/{self.repository}: HTTP {error.code} {sorted(codes)}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Registry unavailable: {self.host}: {error}") from error

    def manifest(self, reference, missing=False):
        response = self.get(f"manifests/{reference}", missing)
        if response is None:
            return None
        raw, headers = response
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if headers.get("Docker-Content-Digest", digest) != digest:
            raise RuntimeError(f"Manifest digest mismatch from {self.image}")
        if reference.startswith("sha256:") and reference != digest:
            raise RuntimeError(f"Registry returned the wrong immutable manifest: {self.image}@{reference}")
        return digest, json.loads(raw)

    def digest(self, reference, missing=False):
        manifest = self.manifest(reference, missing)
        return manifest[0] if manifest else None

    def labels(self, digest):
        manifest = self.manifest(digest)[1]
        if "manifests" in manifest:
            instances = [entry for entry in manifest["manifests"]
                         if entry.get("platform", {}).get("os") == "linux"
                         and entry.get("platform", {}).get("architecture") == "amd64"]
            if len(instances) != 1:
                raise RuntimeError(f"Expected one linux/amd64 SDK in {self.image}@{digest}")
            manifest = self.manifest(instances[0]["digest"])[1]
        config_digest = manifest["config"]["digest"]
        raw, _ = self.get(f"blobs/{config_digest}")
        if "sha256:" + hashlib.sha256(raw).hexdigest() != config_digest:
            raise RuntimeError(f"Config digest mismatch from {self.image}")
        return json.loads(raw).get("config", {}).get("Labels") or {}


def dockerhub():
    return Registry("registry-1.docker.io", "lochnair/openwrt-sdk-rust",
                    os.environ.get("DOCKERHUB_USERNAME", ""), os.environ.get("DOCKERHUB_TOKEN", ""))


def ghcr():
    return Registry("ghcr.io", "lochnair/openwrt-sdk-rust",
                    os.environ.get("GHCR_USERNAME", ""), os.environ.get("GHCR_TOKEN", ""))


def synchronize(tag, source, destination, expected_digest=None):
    digest = source.digest(tag, missing=True)
    if not digest:
        raise RuntimeError(f"Docker Hub source image is missing: {source.image}:{tag}")
    if expected_digest and digest != expected_digest:
        raise RuntimeError(f"Docker Hub tag changed since planning: {tag}")
    if destination.digest(tag, missing=True) != digest:
        subprocess.run([
            "skopeo", "copy", "--all", "--preserve-digests",
            "--authfile", os.environ["REGISTRY_AUTH_FILE"],
            f"docker://{source.image}@{digest}", f"docker://{destination.image}:{tag}",
        ], check=True)
    # Check both mutable tags after the copy, including the already-current case.
    if source.digest(tag) != digest or destination.digest(tag) != digest:
        raise RuntimeError(f"Registry digest verification failed for {tag}")
    print(f"Verified {tag}: Docker Hub = GHCR = {digest}")


if __name__ == "__main__":
    synchronize(sys.argv[1], dockerhub(), ghcr(), sys.argv[2] if len(sys.argv) > 2 else None)
