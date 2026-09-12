"""Parse the inline module scripts in admin templates.

These blocks carry the whole authoring UI -- upload, library, autosave, tag
autocomplete -- and a single syntax error anywhere in a module aborts it
silently, taking every feature after the error with it. Nothing else in the
suite would notice: the endpoints keep passing and the page still renders.

Skipped when no node is available, so CI is not blocked by it.
"""
import os
import re
import shutil
import subprocess
import tempfile

import pytest

SCRATCH_NODE = ('/tmp/claude-1000/-home-corey-trulyverdant/'
                '194eb5b9-7083-4177-8e7a-27d160388640/scratchpad/node/bin/node')


def _node():
    found = shutil.which('node')
    if found:
        return found
    return SCRATCH_NODE if os.access(SCRATCH_NODE, os.X_OK) else None


needs_node = pytest.mark.skipif(_node() is None, reason='no node available')


@needs_node
def test_editor_module_scripts_parse(client, login, author):
    node = _node()
    login('authoruser')
    html = client.get('/admin/articles/new').get_data(as_text=True)
    blocks = re.findall(r'<script type="module">(.*?)</script>', html, re.S)
    assert blocks, 'no module scripts found on the editor page'

    workdir = tempfile.mkdtemp()
    for i, js in enumerate(blocks, 1):
        path = os.path.join(workdir, f'block{i}.mjs')
        with open(path, 'w') as handle:
            handle.write(js)
        result = subprocess.run([node, '--check', path],
                                capture_output=True, text=True)
        assert result.returncode == 0, \
            f'block {i} has a syntax error:\n{result.stderr}'


@needs_node
def test_site_js_parses(app):
    """The public script: theme toggle, nav, cookie consent."""
    node = _node()
    path = os.path.join(app.static_folder, 'js', 'site.js')
    result = subprocess.run([node, '--check', path],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
