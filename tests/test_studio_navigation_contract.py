"""Static contract checks for the current Studio navigation and unified setup page."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "src/web/static/index.html").read_text(encoding="utf-8")
ENHANCEMENTS = (ROOT / "src/web/static/studio-enhancements.js").read_text(encoding="utf-8")


def test_navigation_has_one_ai_configuration_entry_and_no_visible_legacy_services_entry():
    nav_source = INDEX.split("// ===== API =====", 1)[0]
    assert "id:'services'" not in nav_source
    assert nav_source.count("id:'agent-config'") == 1
    assert "label:'全局创作参数'" in nav_source
    assert "label:'模型配置'" not in nav_source


def test_book_navigation_is_grouped_and_unavailable_pages_are_not_fake_buttons():
    assert "navGroup" in nav_source_after_nav_definition()
    assert "if(!navPageAvailable(item)) return '';" in INDEX
    assert "nav-extension-group" in INDEX
    assert "addNav({ id: 'extension-home'" not in ENHANCEMENTS
    assert "addNav({ id: 'agent-config'" not in ENHANCEMENTS


def test_services_is_only_a_deep_link_compatibility_alias():
    assert "if(typeof PAGES['agent-config']==='function') return PAGES['agent-config'](p);" in INDEX
    assert "PAGES.services = PAGES['agent-config'];" in ENHANCEMENTS


def nav_source_after_nav_definition() -> str:
    return INDEX.split("// ===== API =====", 1)[0]
