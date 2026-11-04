"""Shared fixtures for legacy gameplay E2Es; onboarding has its own real UI suite."""

import os


def remember_guides(page):
    """Use the returning-player fixture so gameplay tests can reach room controls."""
    page.evaluate("""() => {
        const user = JSON.parse(localStorage.getItem('gamehub_user'));
        if (!user || user.id == null) throw new Error('missing authenticated user');
        for (const game of ['stasis', 'luzhanqi', 'battleship', 'uno'])
            localStorage.setItem(`gamehub:guide:v1:${user.id}:${game}`, 'seen');
    }""")


def assert_bots_ready(page, code):
    """Read authoritative readiness rather than a deliberately removed UI label."""
    token = page.evaluate("localStorage.getItem('gamehub_token')")
    api = os.environ.get('GAMEHUB_TEST_API', 'http://localhost:50541')
    response = page.request.get(f'{api}/api/rooms/{code}',
                                headers={'Authorization': f'Bearer {token}'})
    assert response.ok, f'room request failed: {response.status}'
    bots = [player for player in response.json()['players'] if player.get('is_bot')]
    assert bots and all(player['ready'] for player in bots), '人机未自动准备'
