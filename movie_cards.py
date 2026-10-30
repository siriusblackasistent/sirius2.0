"""Movie cards use the existing Rezka catalog and local media controller."""
import json
import secrets
import subprocess
import time
import threading
import re
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal
from urllib.parse import urlparse
from sirius_settings import BASE_DIR, media_python, get_settings

router = APIRouter()
items = {}
lock = threading.RLock()
_media = get_settings('media')
MEDIA = 'http://{}:{}'.format(_media.get('host') or '127.0.0.1', _media.get('port') or 8766)


def movie_request(text):
    normalized = ' '.join(text.casefold().split()).strip().rstrip('.!?')
    if normalized in {'популярний', 'популярне', 'популярні', 'популярні фільми',
                       'що популярне', 'що подивитись', 'що подивитися'}:
        return 'popular', ''
    match = re.fullmatch(
        r'/?(?:пошук|з\s*найд[иі](?:ть|те)?|знайти|найд[иі](?:те)?)\s+'
        r'(?:(?:мені|нам)\s+)?(?:(?:фільм|серіал)\s+)?(.+)', normalized)
    return ('search', match[1].strip()) if match else None


def search_movies(text):
    request = movie_request(text)
    if request is None:
        return None
    mode, query = request
    args = ['popular'] if mode == 'popular' else ['search', query]
    try:
        result = subprocess.run([media_python(),str(BASE_DIR / 'rezka_catalog.py'),*args,'--limit','5'], capture_output=True,text=True,timeout=45,check=True)
        found = json.loads(result.stdout)
        return movie_results(found)
    except (subprocess.SubprocessError,ValueError,KeyError,OSError,TypeError):
        raise HTTPException(502,'Не вдалося отримати фільми. Спробуй пізніше.')

def movie_results(found):
    cards = []
    with lock:
        for token in list(items):
            if items[token]['expires'] < time.monotonic(): del items[token]
        for item in found[:5]:
            token = secrets.token_urlsafe(24)
            poster = item.get('poster','')
            if urlparse(poster).scheme not in ('http','https'): poster = ''
            cards.append({'id':token,'name':str(item['name']),'year':str(item.get('year','')),'poster':('/assistant/movie/poster/' + token) if poster else ''})
            items[token] = {**item,'expires':time.monotonic()+3600}
        while len(items)>500: del items[next(iter(items))]
    return {'text':'Обери фільм і місце перегляду, мяу.' if cards else 'Нічого не знайшов, мяу. Спробуй іншу назву.', 'movies':cards,'emotion':'happy'}

class MovieAction(BaseModel):
    id: str
    action: Literal['play','pause','stop','forward','back','speed_10','speed_normal','quality','voice','voice_index','next_episode','previous_episode']
    value: int | None = None
    home: Literal['0','30'] = '30'

@router.post('/assistant/movie/action')
def movie_action(payload: MovieAction):
    with lock:
        item=items.get(payload.id)
        if not item or item['expires']<time.monotonic():
            raise HTTPException(410,'Картка застаріла. Знайди фільм ще раз.')
        if payload.action!='play' and not item.get('active'):
            raise HTTPException(409,'Ця картка не керує поточним відтворенням.')
        if payload.action == 'quality' and payload.value not in (420,480,720,1080):
            raise HTTPException(422,'Невідома якість')
        if payload.action == 'voice_index' and payload.value not in {v['index'] for v in item.get('state',{}).get('voices',[])}:
            raise HTTPException(422,'Онови список озвучок')
        endpoint='/play' if payload.action=='play' else '/control'
        body={'url':item['url'],'title':item['name'],'home':payload.home,'quality':1080} if payload.action=='play' else {'action':payload.action,'value':payload.value}
        try:
            response=requests.post(MEDIA+endpoint,json=body,timeout=(3,60))
            data=response.json()
            if not response.ok: raise HTTPException(502,str(data.get('error','Помилка програвача')))
        except requests.Timeout:
            raise HTTPException(504,'Програвач не відповів вчасно. Перевір екран перед повторним натисканням.')
        except (requests.RequestException,ValueError):
            raise HTTPException(502,'Медіасервер недоступний.')
        if payload.action=='play':
            for entry in items.values(): entry['active']=False
            item['active']=True
        item['expires']=time.monotonic()+43200
        state = {} if payload.action == 'play' else item.get('state', {}).copy()
        state.update({k:v for k,v in data.items() if k != 'message'})
        if payload.action in ('play','quality','voice','voice_index','next_episode','previous_episode'):
            state['paused'] = False
        if payload.action == 'stop':
            item['active'] = False
        state['stopped'] = payload.action == 'stop'
        item['state'] = state
        return {**state, 'text':data.get('message','Готово'), 'id':payload.id}


@router.get('/assistant/movie/poster/{token}')
def movie_poster(token: str):
    from fastapi.responses import Response
    with lock:
        item = items.get(token)
        if not item or item['expires'] < time.monotonic():
            raise HTTPException(404, 'Постер недоступний')
        url = item.get('poster', '')
    parsed = urlparse(url)
    if parsed.scheme not in ('https','http') or parsed.hostname != 'statichdrezka.ac' or parsed.port not in (None,80,443) or parsed.username:
        raise HTTPException(404, 'Постер недоступний')
    try:
        with requests.get(url,timeout=(3,10),stream=True,allow_redirects=False) as response:
            if response.status_code != 200:
                raise HTTPException(502,'Постер недоступний')
            mime = response.headers.get('Content-Type','').split(';')[0]
            if mime not in ('image/jpeg','image/png','image/webp','image/avif'):
                raise HTTPException(502,'Невідомий формат постера')
            data = bytearray()
            for chunk in response.iter_content(32768):
                data.extend(chunk)
                if len(data) > 3 * 1024 * 1024:
                    raise HTTPException(502,'Завеликий постер')
            return Response(bytes(data),media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    except requests.RequestException:
        raise HTTPException(502,'Постер недоступний')
