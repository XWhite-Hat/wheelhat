"""The pre-Qt bootstrap: install detection, wheel choice and integrity gates.

The GUI is exercised by running the real executable. What is unit-tested here
is the logic that decides whether an install is usable, which wheel to fetch,
and whether a download may be trusted - the parts that decide whether a user
gets a working app or a confusing failure.
"""

import hashlib
import json
import os
import zipfile

import pytest

from wheelhat.bootstrap import check, downloader


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "WheelHat"


def make_install(root, *, pyside=True, shiboken=True, sentinel="6.11.2"):
    """Build a fake PySide6 install with selectable pieces missing."""
    target = root / check.PYSIDE_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    if pyside:
        (target / "PySide6").mkdir(exist_ok=True)
    if shiboken:
        (target / "shiboken6").mkdir(exist_ok=True)
    if sentinel is not None:
        (target / downloader.VERSION_SENTINEL).write_text(sentinel, encoding="utf-8")
    return target


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


# ------------------------------------------------------------- completeness


def test_a_finished_install_is_accepted(data_dir):
    make_install(data_dir)
    assert check.install_looks_complete(data_dir) is True


def test_a_missing_install_is_rejected(data_dir):
    data_dir.mkdir(parents=True)
    assert check.install_looks_complete(data_dir) is False


def test_an_interrupted_download_is_rejected(data_dir):
    """Directories exist but the sentinel does not - the classic partial install."""
    make_install(data_dir, sentinel=None)
    assert check.install_looks_complete(data_dir) is False


def test_a_half_extracted_install_is_rejected(data_dir):
    make_install(data_dir, shiboken=False)
    assert check.install_looks_complete(data_dir) is False


def test_installed_version_is_read_back(data_dir):
    make_install(data_dir, sentinel="6.10.0")
    assert check.installed_version(data_dir) == "6.10.0"


def test_installed_version_is_blank_when_absent(data_dir):
    assert check.installed_version(data_dir) == ""


# ------------------------------------------------------------ bootstrap file


def test_bootstrap_config_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    check.write_bootstrap_config("D:/Streaming/WheelHat", "6.11.2")
    stored = check.read_bootstrap_config()
    assert stored["data_dir"] == "D:/Streaming/WheelHat"
    assert stored["pyside6_version"] == "6.11.2"


def test_bootstrap_config_survives_corruption(tmp_path, monkeypatch):
    """A truncated write must not stop the app starting."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = check.bootstrap_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert check.read_bootstrap_config() == {}


def test_writing_config_keeps_the_known_version(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    check.write_bootstrap_config("C:/a", "6.11.2")
    check.write_bootstrap_config("C:/b")
    assert check.read_bootstrap_config()["pyside6_version"] == "6.11.2"


def test_config_path_falls_back_without_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert check.bootstrap_config_path().suffix == ".json"


def test_run_does_nothing_when_not_frozen(monkeypatch):
    """From source, PySide6 comes from the venv and there is nothing to set up."""
    monkeypatch.delattr("sys.frozen", raising=False)
    before = dict(os.environ)
    assert check.run() is None
    assert dict(os.environ) == before


# --------------------------------------------------------------- wheel choice


def _wheel_payload(filenames):
    return {
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "filename": name,
                "url": "https://example.invalid/" + name,
                "digests": {"sha256": "a" * 64},
            }
            for name in filenames
        ]
    }


def _wheel_info_with(monkeypatch, payload, package):
    monkeypatch.setattr(
        downloader.urllib.request, "urlopen", lambda *a, **k: FakeResponse(payload)
    )
    monkeypatch.setattr(downloader.platform, "machine", lambda: "AMD64")
    return downloader._wheel_info(package, "6.11.2")


def test_shiboken_prefers_the_abi3_wheel(monkeypatch):
    """The cp-specific shiboken6 wheel has no __init__.py and breaks Qt."""
    payload = _wheel_payload([
        "shiboken6-6.11.2-cp312-cp312-win_amd64.whl",
        "shiboken6-6.11.2-cp39-abi3-win_amd64.whl",
    ])
    url, _digest, err = _wheel_info_with(monkeypatch, payload, "shiboken6")
    assert err == ""
    assert "abi3" in url


def test_platform_mismatch_is_reported(monkeypatch):
    payload = _wheel_payload(["PySide6-6.11.2-cp312-cp312-manylinux_2_28_x86_64.whl"])
    url, _digest, err = _wheel_info_with(monkeypatch, payload, "PySide6")
    assert url is None
    assert "no wheel for this platform" in err


def test_a_wheel_without_a_hash_is_refused(monkeypatch):
    payload = {
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "filename": "PySide6-6.11.2-cp312-cp312-win_amd64.whl",
                "url": "https://example.invalid/x.whl",
                "digests": {},
            }
        ]
    }
    url, _digest, err = _wheel_info_with(monkeypatch, payload, "PySide6")
    assert url is None
    assert "no SHA-256" in err


# ------------------------------------------------------------- integrity gate


def test_hash_check_accepts_a_matching_file(tmp_path):
    blob = tmp_path / "wheel.whl"
    blob.write_bytes(b"payload")
    digest = hashlib.sha256(b"payload").hexdigest()
    assert downloader._sha256_matches(blob, digest) is True
    assert downloader._sha256_matches(blob, digest.upper()) is True


def test_hash_check_rejects_a_tampered_file(tmp_path):
    blob = tmp_path / "wheel.whl"
    blob.write_bytes(b"payload")
    assert downloader._sha256_matches(blob, hashlib.sha256(b"other").hexdigest()) is False


def test_hash_check_rejects_a_missing_digest(tmp_path):
    """PyPI not returning a hash must fail closed, never open."""
    blob = tmp_path / "wheel.whl"
    blob.write_bytes(b"payload")
    assert downloader._sha256_matches(blob, None) is False
    assert downloader._sha256_matches(blob, "") is False


def test_socket_check_fails_open_when_unreachable(monkeypatch):
    """A Socket.dev outage must never block a legitimate install."""

    def explode(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(downloader.urllib.request, "urlopen", explode)
    passed, note = downloader._socket_check("pyside6", "6.11.2")
    assert passed is True
    assert "unreachable" in note


def test_socket_check_blocks_a_malware_alert(monkeypatch):
    payload = {"score": 0.99, "alerts": [{"type": "malware"}]}
    monkeypatch.setattr(
        downloader.urllib.request, "urlopen", lambda *a, **k: FakeResponse(payload)
    )
    passed, note = downloader._socket_check("pyside6", "6.11.2")
    assert passed is False
    assert "malware" in note


def test_socket_check_blocks_a_low_score(monkeypatch):
    monkeypatch.setattr(
        downloader.urllib.request, "urlopen", lambda *a, **k: FakeResponse({"score": 12})
    )
    passed, note = downloader._socket_check("pyside6", "6.11.2")
    assert passed is False
    assert "below" in note


def test_version_sorting_is_numeric_not_lexical():
    versions = ["6.9.0", "6.10.1", "6.8.1", "6.10.0"]
    assert sorted(versions, key=downloader._version_tuple, reverse=True)[0] == "6.10.1"


# ---------------------------------------------------------------- extraction


def test_download_wipes_a_stale_install_first(tmp_path, monkeypatch):
    """Extracting over an old install leaves a mix of versions that cannot work."""
    target = tmp_path / "pyside6"
    leftover = target / "PySide6" / "old-version.dll"
    leftover.parent.mkdir(parents=True)
    leftover.write_text("stale", encoding="utf-8")

    monkeypatch.setattr(downloader, "_find_safe_version", lambda cb: (None, "stopped"))
    ok, _message = downloader.download_pyside6(target)

    assert ok is False
    assert not leftover.exists(), "the stale install should have been removed first"


def test_a_successful_download_writes_the_sentinel(tmp_path, monkeypatch):
    target = tmp_path / "pyside6"

    def fake_download(url, destination, progress_cb=None):
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr(destination.stem.replace("_dl_", "") + "/__init__.py", "")
        return True, ""

    monkeypatch.setattr(downloader, "_find_safe_version", lambda cb: ("6.11.2", ""))
    monkeypatch.setattr(downloader, "_wheel_info", lambda p, v: ("https://x.invalid/w", "d", ""))
    monkeypatch.setattr(downloader, "_download", fake_download)
    monkeypatch.setattr(downloader, "_sha256_matches", lambda path, digest: True)

    ok, version = downloader.download_pyside6(target)
    assert ok is True
    assert version == "6.11.2"
    assert (target / downloader.VERSION_SENTINEL).read_text(encoding="utf-8") == "6.11.2"
    assert not list(target.glob("_dl_*.whl")), "downloaded wheels should be cleaned up"


def test_a_bad_hash_aborts_and_leaves_no_sentinel(tmp_path, monkeypatch):
    target = tmp_path / "pyside6"

    def fake_download(url, destination, progress_cb=None):
        destination.write_bytes(b"not really a wheel")
        return True, ""

    monkeypatch.setattr(downloader, "_find_safe_version", lambda cb: ("6.11.2", ""))
    monkeypatch.setattr(downloader, "_wheel_info", lambda p, v: ("https://x.invalid/w", "d", ""))
    monkeypatch.setattr(downloader, "_download", fake_download)
    monkeypatch.setattr(downloader, "_sha256_matches", lambda path, digest: False)

    ok, message = downloader.download_pyside6(target)
    assert ok is False
    assert "SHA-256 mismatch" in message
    assert not (target / downloader.VERSION_SENTINEL).exists()
    assert not list(target.glob("_dl_*.whl")), "the rejected wheel should be deleted"


def test_ensure_streams_survives_a_windowed_build(monkeypatch):
    """A windowed PyInstaller build has no stdout; the first print must not die."""
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    check.ensure_streams()
    import sys as _sys

    assert _sys.stdout is not None
    assert _sys.stderr is not None
    print("this must not raise")  # the exact call that used to kill the bootstrap
