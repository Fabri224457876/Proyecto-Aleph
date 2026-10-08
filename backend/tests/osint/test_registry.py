import pytest

from aleph.core.config import Settings
from aleph.osint import registry
from aleph.osint.registry import get_tool, is_async_entrypoint, list_tools, resolve_entrypoint

EXPECTED = {
    "image_meta", "doc_meta", "wayback", "dorks", "typosquat", "favicon", "asn",
    "wallet", "vulns", "ransomware", "evidence", "email_intel", "phone_intel",
}


def test_all_thirteen_tools_are_registered_once():
    names = [tool.name for tool in list_tools()]
    assert set(names) == EXPECTED
    assert len(names) == len(set(names)) == 13


@pytest.mark.parametrize("tool", list_tools(), ids=lambda t: t.name)
def test_every_entrypoint_resolves_and_matches_its_kind(tool):
    assert tool.title.strip() and tool.description.strip()
    func = resolve_entrypoint(tool)
    assert callable(func)
    assert is_async_entrypoint(tool) == tool.is_async


@pytest.mark.parametrize("tool", list_tools(), ids=lambda t: t.name)
def test_inputs_are_declared(tool):
    assert tool.inputs and all(isinstance(item, str) and item for item in tool.inputs)


def test_no_tool_requires_a_key_and_optional_keys_exist_in_settings():
    assert not any(tool.requires_key for tool in list_tools())
    settings_fields = set(Settings.model_fields)
    for tool in list_tools():
        for key in tool.key_settings:
            assert key in settings_fields


def test_network_tools_are_the_ones_that_query_services():
    network = {tool.name for tool in list_tools() if tool.network}
    assert network == {"wayback", "typosquat", "favicon", "asn", "wallet", "vulns", "ransomware", "email_intel"}


def test_lookup_by_name():
    assert get_tool("wallet").entrypoint == "aleph.osint.wallet:analyze_wallet"
    with pytest.raises(KeyError):
        get_tool("no-existe")


def test_registry_is_read_only_tuple():
    assert isinstance(registry.TOOLS, tuple)
