from md_editor.image_sources import parse_srcset, resolve_srcsets
from md_editor.rendering import render_markdown


def test_srcset_keeps_data_uri_commas_and_descriptors():
    assert parse_srcset("data:image/png;base64,AAAA 1x, img/b.png 2x, img/c.png 800w") == [
        ("data:image/png;base64,AAAA", "1x"),
        ("img/b.png", "2x"),
        ("img/c.png", "800w"),
    ]
    assert parse_srcset("a.png, b.png,") == [("a.png", ""), ("b.png", "")]


def test_candidate_urls_resolve_without_touching_code_or_other_markup():
    fragment = '<picture><source srcset="img/a.png 1x, img/b.png 2x"><img src="img/a.png"></picture><code>&lt;img srcset="x"&gt;</code>'
    result = resolve_srcsets(fragment, "file:///C:/notes/")
    assert 'srcset="file:///C:/notes/img/a.png 1x, file:///C:/notes/img/b.png 2x"' in result
    assert '<img src="img/a.png">' in result
    assert '<code>&lt;img srcset="x"&gt;</code>' in result


def test_sanitizer_retains_picture_candidates_but_rejects_active_urls():
    source = '<picture><source media="(min-width: 600px)" srcset="img/a.png 1x, javascript:alert(1) 2x"><img src="data:image/png;base64,AAAA" onerror="bad()"></picture><a href="data:text/html,bad">link</a>'
    html = render_markdown(source).html
    assert "<picture>" in html and 'srcset="img/a.png 1x"' in html
    assert 'src="data:image/png;base64,AAAA"' in html
    assert "javascript" not in html and "onerror" not in html and "data:text/html" not in html


def test_non_image_data_is_not_accepted_as_an_image_candidate():
    html = render_markdown('<img src="data:text/html,bad" srcset="data:text/html,bad 1x">').html
    assert "src=" not in html and "srcset=" not in html
