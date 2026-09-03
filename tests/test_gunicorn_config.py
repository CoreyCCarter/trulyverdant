"""deploy/gunicorn.conf.py is executed by gunicorn before the app is
imported, so it must load .env itself. When it did not, GUNICORN_BIND in
.env was silently ignored and the app listened on loopback -- which from
another host is indistinguishable from the app being down."""
import os
import textwrap

CONF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'deploy', 'gunicorn.conf.py')


def _load(tmp_path, env_text, environ=None):
    (tmp_path / '.env').write_text(textwrap.dedent(env_text))
    deploy = tmp_path / 'deploy'
    deploy.mkdir()
    target = deploy / 'gunicorn.conf.py'
    target.write_text(open(CONF).read())
    ns = {'__file__': str(target)}
    old = dict(os.environ)
    try:
        for k in list(os.environ):
            if k.startswith('GUNICORN_'):
                del os.environ[k]
        os.environ.update(environ or {})
        exec(compile(target.read_text(), str(target), 'exec'), ns)
    finally:
        os.environ.clear()
        os.environ.update(old)
    return ns


def test_bind_comes_from_dotenv(tmp_path):
    ns = _load(tmp_path, 'GUNICORN_BIND=10.100.0.9:8000\n')
    assert ns['bind'] == '10.100.0.9:8000'


def test_real_env_var_beats_the_file(tmp_path):
    ns = _load(tmp_path, 'GUNICORN_BIND=10.100.0.9:8000\n',
               {'GUNICORN_BIND': '127.0.0.1:9999'})
    assert ns['bind'] == '127.0.0.1:9999'


def test_defaults_to_loopback_when_unset(tmp_path):
    """An unconfigured host must not expose itself."""
    ns = _load(tmp_path, '\n')
    assert ns['bind'] == '127.0.0.1:8000'


def test_worker_count_is_bounded(tmp_path):
    """cpu_count*2+1 over-provisions badly for gthread on a small VM."""
    ns = _load(tmp_path, '\n')
    assert 2 <= ns['workers'] <= 4
    assert ns['worker_class'] == 'gthread'
