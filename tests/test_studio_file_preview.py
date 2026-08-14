from pathlib import Path
import re


INDEX_HTML = Path(__file__).parents[1] / "src" / "web" / "static" / "index.html"


def test_studio_visual_assets_fallback_when_opened_as_a_local_file() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert re.search(
        r'<link[^>]+href="/static/studio-theme\.css(?:\?[^\"]+)?"[^>]+onerror="[^\"]*studio-theme\.css',
        html,
    )
    assert "(function(){\n  function loadStudioAsset" in html
    assert re.search(
        r"loadStudioAsset\('/static/studio-visual\.js(?:\?[^']+)?','studio-visual\.js'",
        html,
    )
    assert "const fallbackScript=document.createElement('script')" in html


def test_studio_file_preview_skips_api_bootstrap_and_explains_http_requirement() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "const IS_FILE_PREVIEW=window.location.protocol==='file:';" in html
    assert "function renderFilePreview" in html
    assert "if(IS_FILE_PREVIEW){setConn(false);return;}" in html
    assert "file-preview-card" in html
