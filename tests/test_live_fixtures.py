from pathlib import Path
import hashlib

from law_mcp.card_source import parse_lsdelegated_xml, project_references, project_article

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _family_counts(parsed):
    out = {}
    for ref in parsed["references"]:
        out[ref["api_family"]] = out.get(ref["api_family"], 0) + 1
    return out


def test_real_pipa_decree_fixture_preserves_every_relation_record():
    parsed = parse_lsdelegated_xml(_read("lsDelegated_289537.xml"))
    assert parsed["meta"]["reference_count"] == 1293
    assert _family_counts(parsed) == {
        "law": 1051,
        "admin_rule": 64,
        "ordinance": 176,
        "institution_rule": 2,
    }


def test_real_criminal_code_range_does_not_leak_sibling_title():
    parsed = parse_lsdelegated_xml(_read("lsDelegated_284025_hyungbeob.xml"))
    hit = next(
        ref for ref in parsed["references"]
        if ref["linked_article"] == "362,363,364"
    )
    assert hit["linked_branch"] == "0,0,0"
    assert hit["linked_article_title"] == ""
    assert "친족 사이의 범행과 고소" not in hit["linked_article_title"]


def test_old_source_mst_keeps_provider_target_as_observation_not_resolved_version():
    old = parse_lsdelegated_xml(_read("lsDelegated_248613_old.xml"))
    new = parse_lsdelegated_xml(_read("lsDelegated_283839.xml"))
    assert old["source"]["seq"] == "248613"
    assert new["source"]["seq"] == "283839"

    old_projected = project_references(old)
    first_law_target = next(
        ref["target"] for ref in old_projected["references"]
        if ref["target"]["api_family"] == "law"
    )
    assert "observed_seq" in first_law_target
    assert "resolved_version" not in first_law_target

    # Captured responses show identical relation payloads across the two source
    # versions. This is why the MCP must not claim historical target binding.
    def relation_body_sha(data: bytes) -> str:
        text = data.decode("utf-8")
        marker = "<위임조문정보>"
        body = text[text.find(marker):]
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    assert relation_body_sha(_read("lsDelegated_248613_old.xml")) == relation_body_sha(
        _read("lsDelegated_283839.xml")
    )


def test_real_fixture_projection_can_filter_one_article_without_global_expansion():
    parsed = parse_lsdelegated_xml(_read("lsDelegated_289537.xml"))
    out = project_references(parsed, article=5, branch=3)
    assert out["references"]
    assert all(r["source_article"] == "5" for r in out["references"])
    assert all(r["source_branch"] == "3" for r in out["references"])
    assert any(r["target"]["api_family"] == "ordinance" for r in out["references"])


def test_real_fixture_repeated_target_headers_keep_the_right_target():
    parsed = parse_lsdelegated_xml(_read("lsDelegated_289537.xml"))
    refs = [
        ref for ref in parsed["references"]
        if ref["source_article"] == "9"
        and ref["source_branch"] == "2"
        and ref["api_family"] == "law"
    ]
    assert any(ref["observed_target_seq"] == "195062" for ref in refs)
    assert any(ref["observed_target_seq"] == "213857" for ref in refs)

    second_group = [
        ref for ref in refs
        if ref["observed_target_seq"] == "213857"
    ]
    assert second_group
    assert all(ref["observed_target_title"] == "개인정보 보호법" for ref in second_group)


def test_real_eflaw_fixture_contains_the_minimal_version_and_temporal_inputs():
    import xml.etree.ElementTree as ET

    root = ET.fromstring(_read("eflaw_283839_JO002900.xml"))
    basic = root.find("./기본정보")
    fields = {
        child.tag: (child.text or "").strip()
        for child in list(basic)
        if len(child) == 0
    }
    units = []
    for unit in root.findall("./조문/조문단위"):
        unit_fields = {
            child.tag: (child.text or "").strip()
            for child in list(unit)
            if len(child) == 0
        }
        units.append({
            "번호": unit_fields.get("조문번호", ""),
            "가지번호": unit_fields.get("조문가지번호", ""),
            "제목": unit_fields.get("조문제목", ""),
            "구분": unit_fields.get("조문여부", ""),
            "시행일": unit_fields.get("조문시행일자", ""),
            "내용": unit_fields.get("조문내용", ""),
        })

    out = project_article(
        {"fields": fields, "dates_normalized": {}, "articles": units},
        {"article_count": len(units)},
        mst="283839",
        effective_date="20260911",
        article=29,
    )
    assert out["source"]["law_id"] == "011357"
    assert out["source"]["promulgation_date"] == "20260310"
    assert out["source"]["promulgation_number"] == "21445"
    assert out["source"]["article_effective_date_text"] == (
        "20270701:제32조의2제1항 단서,제75조제2항제15호"
    )
    assert out["article"]["number"] == "29"
    assert out["article"]["effective_date"] == "20260911"
    assert out["article"]["text"].startswith("제29조")


def _walk_structure(nodes):
    for node in nodes:
        yield node
        yield from _walk_structure(node.get("children") or [])


def test_real_eflaw_article75_preserves_provider_hierarchy_without_text_reparse():
    from law_mcp.card_source import parse_eflaw_article_xml

    out = parse_eflaw_article_xml(
        _read("eflaw_full_283839_pipa.xml"),
        mst="283839",
        effective_date="20260911",
        article=75,
    )
    nodes = list(_walk_structure(out["article"]["structure"]))
    assert out["article"]["key"] == "0075001"
    assert sum(n["type"] == "paragraph" for n in nodes) == 5
    assert sum(n["type"] == "item" for n in nodes) == 49
    assert sum(n["type"] == "subitem" for n in nodes) == 0
    assert out["source"]["provider_document_type"] == "법률"
    assert out["source"]["provider_document_type_code"] == "A0002"
    assert out["meta"]["response_sha256"]
    # The provider-level temporal exception remains raw for the downstream TemporalRule stage.
    assert "제75조제2항제15호" in out["source"]["article_effective_date_text"]


def test_real_eflaw_transparent_hang_wrapper_does_not_create_phantom_paragraph():
    from law_mcp.card_source import parse_eflaw_article_xml

    out = parse_eflaw_article_xml(
        _read("eflaw_full_283839_pipa.xml"),
        mst="283839",
        effective_date="20260911",
        article=2,
    )
    top = out["article"]["structure"]
    # Official XML has an empty <항> wrapper around ten directly enumerated 호.
    # The transport projection must not turn that wrapper into a fake paragraph.
    assert len(top) == 10
    assert all(n["type"] == "item" for n in top)
    assert top[0]["label"].startswith("1")
    assert out["article"]["head_text"].startswith("제2조(정의)")


def test_real_eflaw_full_ladder_is_preserved_article_paragraph_item_subitem():
    from law_mcp.card_source import parse_eflaw_article_xml

    out = parse_eflaw_article_xml(
        _read("josub_04_eflawjosub_full_ladder_MOK.xml"),
        mst="193412",
        effective_date="20171019",
        article=3,
    )
    p = out["article"]["structure"][0]
    i = p["children"][0]
    m = i["children"][0]
    assert (p["type"], p["label"]) == ("paragraph", "①")
    assert (i["type"], i["label"]) == ("item", "2.")
    assert (m["type"], m["label"]) == ("subitem", "다.")
    assert m["text"].endswith("플랫폼")


def test_real_eflaw_branch_article_keeps_items_direct_under_article():
    from law_mcp.card_source import parse_eflaw_article_xml

    out = parse_eflaw_article_xml(
        _read("josub_07_branch_article_000402.xml"),
        mst="289537",
        effective_date="20260911",
        article=4,
        branch=2,
    )
    assert out["article"]["branch"] == "2"
    assert [n["type"] for n in out["article"]["structure"]] == ["item", "item"]


def test_lsdelegated_preserves_provider_fragment_group_and_order_without_merging():
    parsed = parse_lsdelegated_xml(_read("lsDelegated_289537.xml"))
    out = project_references(parsed, article=15)
    refs = [
        r for r in out["references"]
        if r.get("context") == "법 제18조제2항" and r.get("at") == "제15조"
    ]
    assert [r["text"] for r in refs] == ["법", "제18조", "제2항"]
    assert len({r["provider_group"] for r in refs}) == 1
    assert [r["provider_order"] for r in refs] == sorted(r["provider_order"] for r in refs)
    # Transport keeps fragments separate. The next stage may decide whether/how
    # to combine them into one ReferenceObservation.
    assert len(refs) == 3


def test_lsdelegated_preserves_repeated_target_segments_inside_one_delegate():
    parsed = parse_lsdelegated_xml(_read("lsDelegated_289537.xml"))
    out = project_references(parsed, article=9, branch=2)
    refs = [r for r in out["references"] if r["target"]["api_family"] == "law"]
    by_seq = {}
    for ref in refs:
        by_seq.setdefault(ref["target"]["observed_seq"], set()).add(ref["provider_group"])
    assert by_seq["195062"]
    assert by_seq["213857"]
    assert by_seq["195062"].isdisjoint(by_seq["213857"])


def test_full_lsdelegated_provider_grouping_is_consistent_and_useful():
    from collections import defaultdict

    out = project_references(parse_lsdelegated_xml(_read("lsDelegated_289537.xml")))
    groups = defaultdict(list)
    for ref in out["references"]:
        groups[ref["provider_group"]].append(ref)

    assert len(groups) == 730
    assert sum(len(v) > 1 for v in groups.values()) == 335
    for group in groups.values():
        identities = {
            (
                r["source_article"], r["source_branch"], r["at"], r.get("context", ""),
                r["target"]["api_family"], r["target"]["observed_seq"],
                r["target"]["observed_title"],
            )
            for r in group
        }
        assert len(identities) == 1
