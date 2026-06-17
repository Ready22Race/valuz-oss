"""④ tool-callback base resolution (Settings.kernel_callback_base_url).

The host-served MCP servers (docs / automations / connectors / harness) are
injected into every session against this base. For a kernel on a SEPARATE
host (cloud sandbox) it must be a host address the kernel can reach — not
loopback. These pin the resolution + the loopback footgun detector.
"""

from valuz_agent.infra.config import Settings


def test_inprocess_ignores_external_url_uses_backend_base() -> None:
    s = Settings(
        kernel_mode="inprocess",
        backend_base_url="http://127.0.0.1:8000",
        host_external_url="http://host.example:9000",
    )
    # In-process: callback is always the host's own URL; external is irrelevant.
    assert s.kernel_callback_base_url == "http://127.0.0.1:8000"
    assert s.kernel_callback_is_loopback is True


def test_http_mode_prefers_external_url() -> None:
    s = Settings(
        kernel_mode="http",
        backend_base_url="http://127.0.0.1:8000",
        host_external_url="http://192.168.1.5:8000",
    )
    assert s.kernel_callback_base_url == "http://192.168.1.5:8000"
    assert s.kernel_callback_is_loopback is False


def test_http_mode_without_external_falls_back_to_loopback() -> None:
    # The NAT footgun: remote kernel, no external URL set → loopback, which
    # boot warns about.
    s = Settings(
        kernel_mode="http",
        backend_base_url="http://127.0.0.1:8000",
        host_external_url=None,
    )
    assert s.kernel_callback_base_url == "http://127.0.0.1:8000"
    assert s.kernel_callback_is_loopback is True


def test_loopback_detector_variants() -> None:
    for url in ("http://localhost:8000", "http://127.0.0.1:1", "http://[::1]:8000"):
        s = Settings(kernel_mode="http", host_external_url=url)
        assert s.kernel_callback_is_loopback is True, url
    s = Settings(kernel_mode="http", host_external_url="https://kernel-host.internal")
    assert s.kernel_callback_is_loopback is False
