"""Temporary adapter: existing voice UI/runtime, guest API and media/catalog.

Run with the existing voice daemon's Python (Whisper dependencies are there).
The old assistant's files and configuration are never modified.
"""
import os
from pathlib import Path
import sys

from sirius_settings import server_config


def forward_command(text, voice=False, client_id=''):
    import requests
    import voice_daemon
    from movie_cards import search_movies, movie_results
    original = text
    corrected = None
    if voice:
        repair = voice_daemon.repair_movie_request(text)
        if repair:
            if repair.get('clarification'):
                result = {'text': repair['clarification'], 'emotion': 'surprised'}
                voice_daemon.remember_local_reply(original, result, client_id)
                return result
            text = repair['command']
            corrected = repair.get('corrected_text')
    result = search_movies(text)
    if result is not None:
        voice_daemon.remember_local_reply(original, result, client_id)
    else:
        response = requests.get(server_config()['baseUrl'].rstrip('/') + '/sendTxtCmd',
            params={'cmd': text, 'chat_id': client_id, 'user_id': client_id}, timeout=(3, 60))
        response.raise_for_status()
        result = response.json()
        if result.get('media_search') is not None:
            result = movie_results(result['media_search']['items'])
        else:
            result = {'text': result.get('text') or 'Команду оброблено.', 'emotion': 'happy'}
    if corrected:
        result['corrected_text'] = corrected
    return result


def main():
    voice_dir = Path(os.environ.get('SIRIUS_VOICE_DIR', ''))
    if not os.environ.get('SIRIUS_VOICE_DIR') or not (voice_dir / 'voice_daemon.py').is_file():
        raise SystemExit('Set SIRIUS_VOICE_DIR to the existing web UI directory')
    # Keep the guest directory first so movie_cards uses guest catalog and media.
    sys.path.append(str(voice_dir))
    os.environ['SIRIUS_MAIN_URL'] = server_config()['baseUrl']
    import voice_daemon
    import uvicorn
    voice_daemon.forward_command = forward_command
    uvicorn.run(voice_daemon.app, host='0.0.0.0', port=5005)


if __name__ == '__main__':
    main()
