"""Theme toggle and mobile-readiness of the rendered markup."""


def test_theme_script_runs_before_stylesheet(client):
    """The pre-paint script must come before the CSS, or the wrong palette
    flashes on first render."""
    html = client.get('/').get_data(as_text=True)
    script = html.index("localStorage.getItem('theme')")
    stylesheet = html.index('css/style.css')
    assert script < stylesheet


def test_theme_toggle_present_with_all_three_states(client):
    html = client.get('/').get_data(as_text=True)
    assert 'id="theme-toggle"' in html
    for icon in ['icon-system', 'icon-light', 'icon-dark']:
        assert icon in html
    assert 'aria-label="Colour theme' in html


def test_theme_color_meta_present(client):
    assert 'name="theme-color"' in client.get('/').get_data(as_text=True)


def test_viewport_is_mobile_ready(client):
    html = client.get('/').get_data(as_text=True)
    assert 'width=device-width, initial-scale=1' in html
    # A maximum-scale or user-scalable=no would block pinch zoom.
    assert 'maximum-scale' not in html
    assert 'user-scalable' not in html


def test_admin_tables_are_wrapped_for_scrolling(client, login, admin):
    login('adminuser')
    for path in ['/admin/', '/admin/people', '/admin/categories']:
        html = client.get(path).get_data(as_text=True)
        if '<table class="table">' in html:
            assert 'table-scroll' in html, path


def test_stylesheet_defines_both_palettes_and_override(app):
    import os
    css = open(os.path.join(app.static_folder, 'css', 'style.css')).read()
    # OS preference, but only when the reader has not chosen light.
    assert ':root:not([data-theme="light"])' in css
    # Explicit choice wins in the other direction too.
    assert ':root[data-theme="dark"]' in css
    # color-scheme keeps native controls and scrollbars in step.
    assert 'color-scheme' in css
    assert 'env(safe-area-inset-bottom)' in css
    assert 'prefers-reduced-motion' in css
