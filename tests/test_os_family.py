"""Tests for OS family normalization (normalize_os_family)."""

from __future__ import annotations

import pytest

from zentinull.normalizer import normalize_os_family


class TestNormalizeOsFamily:
    """Parametrized unit tests for normalize_os_family().

    Covers Windows editions collapsing, macOS variants, iOS/iPadOS,
    Android, Linux distro families, sentinel values → empty, unknown
    OS passthrough, and None input.
    """

    @pytest.mark.parametrize(
        "value,expected",
        [
            # ── Windows editions collapse to "windows" ──────────────────────
            pytest.param("Windows 10 Pro", "windows", id="win10-pro"),
            pytest.param("Windows 10 Enterprise", "windows", id="win10-enterprise"),
            pytest.param("Microsoft Windows 11", "windows", id="win11"),
            pytest.param("windows", "windows", id="win-lowercase"),
            pytest.param("Windows Server 2019", "windows", id="win-server"),
            pytest.param("Windows 7 Professional", "windows", id="win7"),
            # ── macOS variants collapse to "macos" ──────────────────────────
            pytest.param("macOS 14 Sonoma", "macos", id="macos-sonoma"),
            pytest.param("Mac OS X 10.15", "macos", id="macos-catalina"),
            pytest.param("Darwin 23.0", "macos", id="macos-darwin"),
            pytest.param("macos", "macos", id="macos-lowercase"),
            pytest.param("macOS", "macos", id="macos-cap"),
            pytest.param("OS X El Capitan", "macos", id="macos-elcap"),
            # ── iOS / iPadOS collapse to "ios" ──────────────────────────────
            pytest.param("iOS 17.1", "ios", id="ios-17"),
            pytest.param("iPadOS 17", "ios", id="ipados-17"),
            # ── Android collapses to "android" ──────────────────────────────
            pytest.param("Android 14", "android", id="android-14"),
            pytest.param("android", "android", id="android-lowercase"),
            pytest.param("Android 13", "android", id="android-13"),
            # ── Linux distros collapse to "linux" ───────────────────────────
            pytest.param("Ubuntu 22.04", "linux", id="linux-ubuntu"),
            pytest.param("Debian 12", "linux", id="linux-debian"),
            pytest.param("CentOS 7", "linux", id="linux-centos"),
            pytest.param("RHEL 9", "linux", id="linux-rhel"),
            pytest.param("Red Hat Enterprise Linux", "linux", id="linux-redhat"),
            pytest.param("Fedora 38", "linux", id="linux-fedora"),
            pytest.param("SUSE Linux Enterprise", "linux", id="linux-suse"),
            pytest.param("linux", "linux", id="linux-plain"),
            # ── Sentinel values → empty string ──────────────────────────────
            pytest.param("", "", id="empty"),
            pytest.param("--", "", id="double-dash"),
            pytest.param("-", "", id="single-dash"),
            pytest.param("N/A", "", id="na-upper"),
            pytest.param("n/a", "", id="na-lower"),
            pytest.param("null", "", id="null-literal"),
            pytest.param("None", "", id="none-literal"),
            # ── Unknown OS → passthrough lowered ────────────────────────────
            pytest.param("Solaris 11", "solaris 11", id="unknown-solaris"),
            pytest.param("FreeBSD 13", "freebsd 13", id="unknown-freebsd"),
            # ── None input → empty string ──────────────────────────────────
            pytest.param(None, "", id="none-object"),
        ],
    )
    def test_normalize_os_family(self, value: str | None, expected: str) -> None:
        assert normalize_os_family(value) == expected
