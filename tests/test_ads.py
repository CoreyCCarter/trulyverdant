"""Ad rendering must be gated on configuration AND consent."""


def _publish(make_article, author):
    return make_article(author, title='Ad Host Article',
                        body='\n\n'.join(f'Paragraph number {i}.'
                                         for i in range(10)))


def test_no_ad_markup_when_unconfigured(client, author, make_article):
    a = _publish(make_article, author)
    html = client.get(a.url).get_data(as_text=True)
    assert 'adsbygoogle' not in html
    assert 'pagead2.googlesyndication.com' not in html


def test_no_ads_before_consent(app, client, author, make_article):
    app.config['ADSENSE_CLIENT_ID'] = 'ca-pub-1234567890123456'
    app.config['ADSENSE_SLOT_IN_ARTICLE'] = '1111111111'
    app.config['REQUIRE_COOKIE_CONSENT'] = True
    a = _publish(make_article, author)
    html = client.get(a.url).get_data(as_text=True)
    # The consent banner shows, but no Google script is emitted yet.
    assert 'cookie' in html.lower()
    assert 'adsbygoogle' not in html
    assert 'pagead2.googlesyndication.com' not in html


def test_ads_render_after_consent(app, client, author, make_article):
    app.config['ADSENSE_CLIENT_ID'] = 'ca-pub-1234567890123456'
    app.config['ADSENSE_SLOT_IN_ARTICLE'] = '1111111111'
    app.config['REQUIRE_COOKIE_CONSENT'] = True
    a = _publish(make_article, author)
    client.set_cookie('cookie_consent', 'accepted', domain='example.test')
    html = client.get(a.url).get_data(as_text=True)
    assert 'pagead2.googlesyndication.com' in html
    assert 'ca-pub-1234567890123456' in html
    assert 'data-ad-slot="1111111111"' in html
    assert 'Advertisement' in html


def test_rejecting_consent_keeps_ads_off(app, client, author, make_article):
    app.config['ADSENSE_CLIENT_ID'] = 'ca-pub-1234567890123456'
    app.config['ADSENSE_SLOT_IN_ARTICLE'] = '1111111111'
    a = _publish(make_article, author)
    client.set_cookie('cookie_consent', 'rejected', domain='example.test')
    html = client.get(a.url).get_data(as_text=True)
    assert 'adsbygoogle' not in html


def test_consent_can_be_waived_by_config(app, client, author, make_article):
    """Outside consent regimes an operator may serve ads without a banner."""
    app.config['ADSENSE_CLIENT_ID'] = 'ca-pub-1234567890123456'
    app.config['ADSENSE_SLOT_IN_ARTICLE'] = '1111111111'
    app.config['REQUIRE_COOKIE_CONSENT'] = False
    a = _publish(make_article, author)
    html = client.get(a.url).get_data(as_text=True)
    assert 'adsbygoogle' in html


def test_short_article_gets_no_in_article_ad(app, client, author,
                                             make_article):
    """A three-paragraph post should not be interrupted by an ad."""
    app.config['ADSENSE_CLIENT_ID'] = 'ca-pub-1234567890123456'
    app.config['ADSENSE_SLOT_IN_ARTICLE'] = '1111111111'
    app.config['REQUIRE_COOKIE_CONSENT'] = False
    a = make_article(author, title='Tiny', body='One para only.')
    html = client.get(a.url).get_data(as_text=True)
    assert 'data-ad-slot="1111111111"' not in html


def test_privacy_policy_covers_adsense_requirements(client):
    """AdSense review expects these disclosures to be present."""
    html = client.get('/privacy').get_data(as_text=True).lower()
    for phrase in ['cookie', 'google', 'advertis', 'third-party',
                   'google.com/settings/ads']:
        assert phrase in html, phrase


def test_privacy_and_contact_linked_from_every_page(client, author,
                                                    make_article):
    a = _publish(make_article, author)
    for path in ['/', a.url, '/about']:
        html = client.get(path).get_data(as_text=True)
        assert '/privacy' in html
        assert '/contact' in html
