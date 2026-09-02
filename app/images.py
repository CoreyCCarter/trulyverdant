"""Image uploads.

Every upload is re-encoded through Pillow rather than stored as received.
That normalises the format, strips EXIF (which can carry GPS coordinates
from a phone camera), and guarantees the bytes really are an image and not
something mislabelled with an image extension.

Each upload produces WebP variants at several widths so templates can emit a
responsive srcset. Smaller images over the wire is the single biggest lever
on Core Web Vitals, which in turn affects ad viewability and revenue.
"""
import os
import secrets

from flask import current_app, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

# Pillow decompression-bomb guard: refuse absurd pixel counts.
Image.MAX_IMAGE_PIXELS = 64_000_000


class ImageError(Exception):
    """Raised when an upload is not a usable image."""


def _upload_dir():
    path = current_app.config['UPLOAD_FOLDER']
    os.makedirs(path, exist_ok=True)
    return path


def allowed_file(filename):
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in current_app.config['ALLOWED_IMAGE_EXTENSIONS']


def save_image(file_storage):
    """Persist an upload, returning the stem shared by all its variants.

    Templates turn that stem into URLs via `image_srcset` / `image_url`.
    """
    if not file_storage or not file_storage.filename:
        raise ImageError('No file was selected.')
    if not allowed_file(file_storage.filename):
        allowed = ', '.join(sorted(
            current_app.config['ALLOWED_IMAGE_EXTENSIONS']))
        raise ImageError(f'Unsupported file type. Allowed: {allowed}.')

    try:
        img = Image.open(file_storage.stream)
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError('That file could not be read as an image.') from exc

    # Honour the EXIF orientation flag, then drop the metadata entirely.
    img = ImageOps.exif_transpose(img)
    if img.mode not in ('RGB', 'RGBA'):
        img = img.convert('RGBA' if 'A' in img.mode else 'RGB')
    if img.mode == 'RGBA':
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background

    stem = secrets.token_urlsafe(12).replace('-', '_')
    directory = _upload_dir()
    widths = current_app.config['IMAGE_WIDTHS']

    written = []
    for width in widths:
        if img.width <= width and written:
            # Never upscale; the largest variant is already covered.
            break
        target = img if img.width <= width else _resize(img, width)
        path = os.path.join(directory, f'{stem}-{target.width}.webp')
        target.save(path, 'WEBP', quality=82, method=5)
        written.append(target.width)

    if not written:
        raise ImageError('The image could not be processed.')

    return f'{stem}:{",".join(str(w) for w in written)}'


def _resize(img, width):
    height = max(1, round(img.height * (width / img.width)))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def _parse(stored):
    """Split the stored 'stem:w1,w2' value into (stem, [widths])."""
    if not stored:
        return None, []
    if ':' not in stored:
        return stored, []
    stem, widths = stored.split(':', 1)
    return stem, [int(w) for w in widths.split(',') if w.isdigit()]


def image_url(stored, width=None):
    """URL for one variant; the largest available if `width` is None."""
    stem, widths = _parse(stored)
    if not stem:
        return None
    if not widths:
        # A plain external URL stored verbatim.
        return stem if stem.startswith('http') else None
    chosen = widths[-1] if width is None else min(
        widths, key=lambda w: (abs(w - width), w))
    return url_for('static', filename=f'uploads/{stem}-{chosen}.webp')


def image_srcset(stored):
    """A `srcset` string, or '' when there is only one variant."""
    stem, widths = _parse(stored)
    if not stem or len(widths) < 2:
        return ''
    return ', '.join(
        f"{url_for('static', filename=f'uploads/{stem}-{w}.webp')} {w}w"
        for w in widths)


def delete_image(stored):
    stem, widths = _parse(stored)
    if not stem or not widths:
        return
    directory = _upload_dir()
    for width in widths:
        path = os.path.join(directory, f'{stem}-{width}.webp')
        if os.path.exists(path):
            os.remove(path)
