"""Authoring aids: body-image upload, the reuse library, tag autocomplete.

Body images previously had no upload path at all -- save_image() was
reachable only through the hero field -- so an image-heavy plant article
could not be illustrated without hand-writing a URL.
"""
import io

from PIL import Image

from app.images import list_images


def _png(size=(1200, 800)):
    buf = io.BytesIO()
    Image.new('RGB', size, (30, 120, 60)).save(buf, 'PNG')
    buf.seek(0)
    return buf


# --- upload endpoint ------------------------------------------------------

def test_upload_requires_login(client):
    r = client.post('/admin/media/upload')
    assert r.status_code == 302 and '/auth/login' in r.headers['Location']


def test_upload_returns_insertable_markdown(client, login, author):
    login('authoruser')
    r = client.post('/admin/media/upload',
                    data={'file': (_png(), 'leaf.png')},
                    content_type='multipart/form-data')
    assert r.status_code == 200
    body = r.get_json()
    assert body['url'].startswith('/static/uploads/')
    assert body['url'].endswith('.webp')
    # Ready to drop straight into the body at the cursor.
    assert body['markdown'] == f"![]({body['url']})"


def test_upload_rejects_a_non_image(client, login, author):
    login('authoruser')
    r = client.post('/admin/media/upload',
                    data={'file': (io.BytesIO(b'#!/bin/sh\nrm -rf /'), 'x.sh')},
                    content_type='multipart/form-data')
    assert r.status_code == 400
    assert 'error' in r.get_json()


def test_upload_rejects_an_extension_lie(client, login, author):
    """A script renamed .png must not be accepted."""
    login('authoruser')
    r = client.post('/admin/media/upload',
                    data={'file': (io.BytesIO(b'not an image'), 'evil.png')},
                    content_type='multipart/form-data')
    assert r.status_code == 400


def test_upload_with_no_file_is_rejected(client, login, author):
    login('authoruser')
    r = client.post('/admin/media/upload', data={},
                    content_type='multipart/form-data')
    assert r.status_code == 400


# --- media library --------------------------------------------------------

def test_library_requires_login(client):
    r = client.get('/admin/media')
    assert r.status_code == 302 and '/auth/login' in r.headers['Location']


def test_library_is_empty_to_begin_with(client, login, author):
    login('authoruser')
    assert client.get('/admin/media').get_json()['images'] == []


def test_library_lists_an_upload(client, login, author):
    login('authoruser')
    client.post('/admin/media/upload', data={'file': (_png(), 'a.png')},
                content_type='multipart/form-data')
    images = client.get('/admin/media').get_json()['images']
    assert len(images) == 1
    entry = images[0]
    assert entry['thumb'].endswith('.webp')
    assert entry['bytes'] > 0


def test_list_images_groups_variants_under_one_entry(app, login, author):
    """save_image writes several widths per upload; the library must show
    one image, not one row per width."""
    from app.images import save_image
    from werkzeug.datastructures import FileStorage
    save_image(FileStorage(stream=_png(), filename='a.png'))
    images = list_images()
    assert len(images) == 1
    assert len(images[0]['widths']) > 1
    assert images[0]['widths'] == sorted(images[0]['widths'])


def test_list_images_ignores_unrelated_files(app):
    """The listing is a directory scan, so it must not choke on strays."""
    import os
    folder = app.config['UPLOAD_FOLDER']
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, '.gitkeep'), 'w').close()
    open(os.path.join(folder, 'notes.txt'), 'w').close()
    assert list_images() == []


# --- editor page ----------------------------------------------------------

def test_editor_carries_the_authoring_hooks(client, login, author):
    login('authoruser')
    html = client.get('/admin/articles/new').get_data(as_text=True)
    for hook in ['id="btn-upload"', 'id="btn-library"', 'id="draft-bar"',
                 'id="tag-suggest"', 'id="all-tags"']:
        assert hook in html, hook


def test_editor_preloads_existing_tags(client, login, author, make_article,
                                       app):
    """Autocomplete is fed from the page, so the names must be embedded."""
    from app.extensions import db
    from app.models import Tag, unique_slug
    db.session.add(Tag(name='monstera', slug=unique_slug(Tag, 'monstera')))
    db.session.commit()
    login('authoruser')
    html = client.get('/admin/articles/new').get_data(as_text=True)
    assert 'monstera' in html.split('id="all-tags"')[1][:200]


def test_editor_warns_against_typing_paths_by_hand(client, login, author):
    login('authoruser')
    html = client.get('/admin/articles/new').get_data(as_text=True)
    assert 'Insert image' in html


# --- the hidden attribute must actually hide ------------------------------

def test_stylesheet_forces_the_hidden_attribute(app):
    """Regression: the UA rule [hidden]{display:none} is specificity 0,1,0,
    the same as a class selector, so a later `.draft-bar{display:flex}`
    silently overrode it. The draft bar was permanently visible and both
    its buttons set el.hidden to no visible effect. No functional test can
    see a cascade conflict, so assert the rule exists."""
    import os
    import re
    css = open(os.path.join(app.static_folder, 'css', 'style.css')).read()
    # Match the declaration itself, not the comment above it that also
    # mentions [hidden] -- which is what this assertion first caught.
    # Strip comments first: prose explaining the rule can contain the same
    # pattern, and an earlier textual match would be examined instead.
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    match = re.search(r'\[hidden\]\s*\{([^}]*)\}', css)
    assert match, 'no [hidden] rule in the stylesheet'
    body = match.group(1)
    assert 'display: none' in body or 'display:none' in body
    assert '!important' in body, 'without !important a component rule wins'
    # It must come before the component rules it has to beat.
    assert match.start() < css.index('.draft-bar')


def test_draft_bar_and_library_start_hidden(client, login, author):
    """Neither should be on screen until there is something to show."""
    login('authoruser')
    html = client.get('/admin/articles/new').get_data(as_text=True)
    for el in ['draft-bar', 'library', 'upload-status']:
        marker = f'id="{el}"'
        assert marker in html
        tag = html[html.index(marker) - 120:html.index(marker) + 160]
        assert 'hidden' in tag, f'{el} is not hidden on first render'
