"""Markdown import: parsing the house layout, front matter, images, and
the safety rules (alt text, no silent overwrites, one transaction per file)."""
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from app.extensions import db
from app.importer import import_directory, import_file, parse_file, ImportProblem
from app.models import Article, Category, Tag

HOUSE = """# Monstera Deliciosa: The Plant That Ate Instagram

*The iconic split-leaf climber that tolerates beginner mistakes.*

**Post date:** September 13, 2026 · **Category:** Easy

Intro paragraph about the plant.

## Watering
Water when the top 2 inches are dry.
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def _jpg(path, size=(1600, 1000)):
    buf = io.BytesIO()
    Image.new('RGB', size, (40, 120, 60)).save(buf, 'JPEG')
    path.write_bytes(buf.getvalue())


def test_parses_house_layout_and_filename(tmp_path):
    p = _write(tmp_path, '2026-09-13_monstera-deliciosa.md', HOUSE)
    meta, body = parse_file(p)
    assert meta['title'].startswith('Monstera Deliciosa')
    assert meta['summary'] == 'The iconic split-leaf climber that tolerates beginner mistakes.'
    assert meta['category'] == 'Easy'
    assert meta['slug'] == 'monstera-deliciosa'
    assert meta['date'] == '2026-09-13'
    # The page renders title, summary and date itself; none may repeat.
    assert '# Monstera' not in body
    assert 'Post date' not in body
    assert 'iconic split-leaf' not in body
    assert body.startswith('Intro paragraph')


def test_front_matter_overrides_the_file(tmp_path):
    text = ("---\ntitle: Custom Title\ncategory: Aroids\n"
            "tags: [climber, aroid]\n---\n" + HOUSE)
    p = _write(tmp_path, '2026-09-13_monstera-deliciosa.md', text)
    meta, _ = parse_file(p)
    assert meta['title'] == 'Custom Title'
    assert meta['category'] == 'Aroids'
    assert meta['tags'] == ['climber', 'aroid']


def test_creates_published_article_with_category_and_tags(app, author, tmp_path):
    when = '2026-09-10T15:42:00+00:00'
    text = f"---\npublished_at: {when}\ntags: climber, Aroid\n---\n" + HOUSE
    p = _write(tmp_path, '2026-09-10_monstera-deliciosa.md', text)
    r = import_file(p, author)
    assert r.action == 'created'
    a = Article.query.filter_by(slug='monstera-deliciosa').one()
    assert a.status == 'published'
    assert a.published_at.replace(tzinfo=timezone.utc) == datetime(2026, 9, 10, 15, 42, tzinfo=timezone.utc)
    assert a.category.name == 'Easy'
    assert sorted(t.name for t in a.tags) == ['aroid', 'climber']
    assert a.summary.startswith('The iconic')


def test_future_date_imports_as_pending(app, author, tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    p = _write(tmp_path, 'x_future.md', f"---\npublished_at: '{future}'\n---\n" + HOUSE)
    r = import_file(p, author)
    assert r.state == 'pending'


def test_category_matched_case_insensitively(app, author, tmp_path):
    db.session.add(Category(name='Easy', slug='easy'))
    db.session.commit()
    p = _write(tmp_path, '2026-09-10_a.md', HOUSE.replace('**Category:** Easy', '**Category:** easy'))
    import_file(p, author)
    assert Category.query.filter(db.func.lower(Category.name) == 'easy').count() == 1


def test_existing_slug_is_skipped_not_overwritten(app, author, tmp_path, make_article):
    make_article(author, title='monstera deliciosa')
    p = _write(tmp_path, '2026-09-10_monstera-deliciosa.md', HOUSE)
    r = import_file(p, author)
    assert r.action == 'skipped'
    assert Article.query.filter_by(slug='monstera-deliciosa').one().title == 'monstera deliciosa'


def test_update_replaces_existing(app, author, tmp_path, make_article):
    make_article(author, title='monstera deliciosa')
    p = _write(tmp_path, '2026-09-10_monstera-deliciosa.md', HOUSE)
    r = import_file(p, author, update=True)
    assert r.action == 'updated'
    assert Article.query.filter_by(slug='monstera-deliciosa').one().title.startswith('Monstera Deliciosa')


def test_dry_run_writes_nothing(app, author, tmp_path):
    p = _write(tmp_path, '2026-09-10_monstera-deliciosa.md', HOUSE)
    r = import_file(p, author, dry_run=True)
    assert r.action == 'would-create'
    assert Article.query.count() == 0
    assert Category.query.count() == 0


def test_uploads_hero_and_body_images(app, author, tmp_path):
    (tmp_path / 'img').mkdir()
    _jpg(tmp_path / 'img' / 'hero.jpg')
    _jpg(tmp_path / 'img' / 'body.jpg')
    text = ("---\nhero_image: img/hero.jpg\nhero_alt: A monstera leaf\n---\n"
            + HOUSE.replace('## Watering', '![Split leaves up close](img/body.jpg)\n\n## Watering'))
    p = _write(tmp_path, '2026-09-10_monstera-deliciosa.md', text)
    r = import_file(p, author)
    assert r.images == 2
    a = Article.query.one()
    assert a.hero_image and a.hero_alt == 'A monstera leaf'
    assert 'img/body.jpg' not in a.body_markdown
    assert '/static/uploads/' in a.body_markdown
    assert 'alt="Split leaves up close"' in a.body_html


def test_hero_without_alt_is_refused(app, author, tmp_path):
    _jpg(tmp_path / 'hero.jpg')
    p = _write(tmp_path, '2026-09-10_a.md', "---\nhero_image: hero.jpg\n---\n" + HOUSE)
    with pytest.raises(ImportProblem):
        import_file(p, author)
    assert Article.query.count() == 0


def test_body_image_without_alt_is_refused(app, author, tmp_path):
    _jpg(tmp_path / 'b.jpg')
    p = _write(tmp_path, '2026-09-10_a.md', HOUSE + '\n![](b.jpg)\n')
    with pytest.raises(ImportProblem):
        import_file(p, author)


def test_missing_image_is_refused(app, author, tmp_path):
    p = _write(tmp_path, '2026-09-10_a.md', "---\nhero_image: nope.jpg\nhero_alt: x\n---\n" + HOUSE)
    with pytest.raises(ImportProblem):
        import_file(p, author)


def test_directory_reports_bad_files_and_imports_the_rest(app, author, tmp_path):
    _write(tmp_path, '2026-09-10_good.md', HOUSE)
    _write(tmp_path, '2026-09-10_bad.md', "---\nhero_image: missing.jpg\nhero_alt: x\n---\n" + HOUSE)
    _write(tmp_path, 'notes.txt', 'ignored')
    results = import_directory(str(tmp_path), author)
    actions = {r.file: r.action for r in results}
    assert actions == {'2026-09-10_bad.md': 'error', '2026-09-10_good.md': 'created'}
    assert Article.query.filter_by(slug='good').count() == 1


def test_cli_command_runs(app, author, tmp_path):
    _write(tmp_path, '2026-09-10_cli-plant.md', HOUSE)
    runner = app.test_cli_runner()
    out = runner.invoke(args=['import-articles', str(tmp_path), '--author', 'authoruser'])
    assert out.exit_code == 0, out.output
    assert 'created' in out.output
    assert Article.query.filter_by(slug='cli-plant').count() == 1
