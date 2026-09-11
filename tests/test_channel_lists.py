"""eco.aliases.channel_lists - the diff logic shared by
Daq.compare_channels() (local namespace) and the status server's
/channels/compare endpoint (see eco.status_server.namespace_store /
namespace_server, and tests/test_daq_status_server.py /
tests/test_status_server.py for the two integration paths)."""

from eco.aliases.channel_lists import (
    compare_channel_lists,
    required_channels_by_type,
)


def test_required_channels_by_type_groups_by_channeltype():
    alias_list = [
        {"alias": "jf03", "channel": "JF03", "channeltype": "JF"},
        {"alias": "jf04", "channel": "JF04", "channeltype": "JF"},
        {"alias": "bs1", "channel": "BS:1", "channeltype": "BS"},
        {"alias": "pv1", "channel": "PV:1", "channeltype": "CA"},
    ]
    grouped = required_channels_by_type(alias_list)
    assert grouped["JF"] == ["JF03", "JF04"]
    assert grouped["BS"] == ["BS:1"]
    assert grouped["CA"] == ["PV:1"]


def test_compare_reports_missing_and_exceeding():
    alias_list = [
        {"alias": "jf03", "channel": "JF03", "channeltype": "JF"},
        {"alias": "jf04", "channel": "JF04", "channeltype": "JF"},
    ]
    recorded = {"channels_JF": ["JF03", "JF_OLD"]}
    result = compare_channel_lists(alias_list, recorded)
    assert set(result) == {"channels_JF"}  # only lists present in `recorded`
    jf = result["channels_JF"]
    assert jf["channeltype"] == "JF"
    assert jf["missing"] == ["JF04"]
    assert jf["exceeding"] == ["JF_OLD"]
    assert jf["n_required"] == 2
    assert jf["n_recorded"] == 2
    # channels_BS/channels_BSCAM were not in `recorded` at all - not
    # fabricated as "everything missing"
    assert "channels_BS" not in result
    assert "channels_BSCAM" not in result


def test_compare_is_ok_when_lists_match_exactly():
    alias_list = [{"alias": "jf03", "channel": "JF03", "channeltype": "JF"}]
    result = compare_channel_lists(alias_list, {"channels_JF": ["JF03"]})
    assert result["channels_JF"]["missing"] == []
    assert result["channels_JF"]["exceeding"] == []


def test_compare_respects_list_names_filter():
    alias_list = [
        {"alias": "jf03", "channel": "JF03", "channeltype": "JF"},
        {"alias": "bs1", "channel": "BS:1", "channeltype": "BS"},
    ]
    recorded = {"channels_JF": ["JF03"], "channels_BS": ["BS:1"]}
    result = compare_channel_lists(alias_list, recorded, list_names=["channels_JF"])
    assert set(result) == {"channels_JF"}


def test_compare_ignores_unknown_list_names():
    result = compare_channel_lists([], {"channels_JF": []}, list_names=["not_a_list"])
    assert result == {}
