from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("sandbox_proxy", Path(__file__).with_name("proxy.py"))
assert spec and spec.loader
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "192.168.1.1"])
def test_private_and_metadata_destinations_are_rejected(host: str) -> None:
    with pytest.raises(PermissionError):
        asyncio.run(proxy._public_destination(host, 443))


def test_public_destination_is_resolved_without_connecting() -> None:
    assert asyncio.run(proxy._public_destination("8.8.8.8", 443)) == ("8.8.8.8", 443)


def test_default_policy_is_deny_with_registry_allowlist(tmp_path) -> None:
    mode, domains = proxy.load_policy({}, str(tmp_path / "missing.json"))
    assert mode == "deny"
    assert proxy.host_allowed("files.pythonhosted.org", mode, domains)
    assert proxy.host_allowed("sub.registry.npmjs.org", mode, domains)
    assert not proxy.host_allowed("evil.com", mode, domains)
    assert not proxy.host_allowed("pypi.org.evil.com", mode, domains)
    assert not proxy.host_allowed("8.8.8.8", mode, domains)
    assert not proxy.host_allowed("[::1]", mode, domains)


def test_policy_file_and_env_extend_allowlist(tmp_path) -> None:
    policy = tmp_path / "p.json"
    policy.write_text('{"allow_domains": ["api.example.com"]}')
    mode, domains = proxy.load_policy({"MOVO_EGRESS_ALLOW_DOMAINS": "Foo.org, "}, str(policy))
    assert proxy.host_allowed("api.example.com", mode, domains)
    assert proxy.host_allowed("x.foo.org", mode, domains)


def test_allow_mode_and_invalid_mode(tmp_path) -> None:
    mode, domains = proxy.load_policy({"MOVO_EGRESS_DEFAULT_POLICY": "allow"}, str(tmp_path / "x"))
    assert proxy.host_allowed("anything.net", mode, domains)
    with pytest.raises(SystemExit):
        proxy.load_policy({"MOVO_EGRESS_DEFAULT_POLICY": "maybe"})


def test_metadata_still_blocked_even_in_allow_mode() -> None:
    with pytest.raises(PermissionError):
        asyncio.run(proxy._public_destination("169.254.169.254", 80))
