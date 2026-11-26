"""인스타 공식 API(Instagram Login)로 캐러셀 게시 + 예약 큐 실행

토큰: 같은 폴더의 ig_token.txt (사용자가 직접 붙여넣음, 다른 곳에 복사하지 말 것)
큐:   같은 폴더의 ig_queue.json
     [{"id": "w3_15_welding", "publish_at": "2026-11-26T11:55:00+09:00",
       "media": ["https://raw.githubusercontent.com/...01.png", ...], "caption": "...", "status": "pending"}]

사용법 (python -I -X utf8 ig_post.py ...)
  check            토큰·계정 확인 + 오늘 남은 게시 한도
  run              publish_at이 지난 pending 항목을 게시 (작업 스케줄러가 10분마다 실행)
  list             큐 상태 보기
  refresh          토큰 연장(60일). run 할 때 20일 지났으면 자동으로도 함
"""
import json, os, sys, time, urllib.request, urllib.parse, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, 'ig_token.txt')
QUEUE_FILE = os.path.join(HERE, 'ig_queue.json')
LOG_FILE = os.path.join(HERE, 'ig_post.log')
STATE_FILE = os.path.join(HERE, 'ig_state.json')   # 토큰 마지막 연장 시각 (토큰 값은 저장 안 함)
CONFIG_FILE = os.path.join(HERE, 'ig_config.json')   # {"mode": "facebook"|"instagram", "ig_user_id": "..."}
_cfg = json.load(open(CONFIG_FILE, encoding='utf-8')) if os.path.exists(CONFIG_FILE) else {}
MODE = _cfg.get('mode', 'instagram')
IG_USER = _cfg.get('ig_user_id', 'me')
API = 'https://graph.facebook.com/v23.0' if MODE == 'facebook' else 'https://graph.instagram.com/v23.0'


CLOUD = bool(os.environ.get('GITHUB_ACTIONS'))   # GitHub Actions에서 실행 중이면 True


def log(msg):
    line = f"{dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    if not CLOUD:   # 공개 저장소라 클라우드에선 로그 파일을 남기지 않음 (Actions 기록에만)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


def token():
    if os.environ.get('IG_TOKEN', '').strip():   # 클라우드: GitHub 저장소 Secret
        return os.environ['IG_TOKEN'].strip()
    tf = os.environ.get('IG_TOKEN_FILE') or TOKEN_FILE   # 노트북 안전장치: buja_tools/ig_token.txt
    if not os.path.exists(tf):
        sys.exit('[토큰 없음] ig_token.txt 에 토큰을 붙여넣어 주세요')
    t = open(tf, encoding='utf-8').read().strip()
    if not t:
        sys.exit('[토큰 비어 있음] ig_token.txt 확인')
    return t


def call(method, path, params=None, base=API):
    params = dict(params or {})
    params['access_token'] = token()
    url = f"{base}/{path}"
    data = None
    if method == 'GET':
        url += '?' + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')
        raise RuntimeError(f'HTTP {e.code} {path}: {body[:500]}')


def wait_ready(cid, tries=30):
    for _ in range(tries):
        st = call('GET', cid, {'fields': 'status_code'}).get('status_code')
        if st == 'FINISHED':
            return
        if st in ('ERROR', 'EXPIRED'):
            raise RuntimeError(f'컨테이너 {cid} 상태 {st}')
        time.sleep(5)
    raise RuntimeError(f'컨테이너 {cid} 준비 시간 초과')


def publish_carousel(media, caption):
    if not 2 <= len(media) <= 10:
        raise ValueError('캐러셀은 이미지 2~10장')
    children = []
    for url in media:
        r = call('POST', f'{IG_USER}/media', {'image_url': url, 'is_carousel_item': 'true'})
        children.append(r['id'])
    for c in children:
        wait_ready(c)
    car = call('POST', f'{IG_USER}/media', {'media_type': 'CAROUSEL', 'children': ','.join(children), 'caption': caption})
    wait_ready(car['id'])
    pub = call('POST', f'{IG_USER}/media_publish', {'creation_id': car['id']})
    link = call('GET', pub['id'], {'fields': 'permalink'}).get('permalink', '')
    return pub['id'], link


def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    return json.load(open(QUEUE_FILE, encoding='utf-8'))


def save_queue(q):
    tmp = QUEUE_FILE + '.tmp'
    json.dump(q, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, QUEUE_FILE)


def refresh(force=False):
    if MODE == 'facebook':   # 시스템 사용자 토큰은 만료가 없어 연장 불필요
        return
    st = json.load(open(STATE_FILE, encoding='utf-8')) if os.path.exists(STATE_FILE) else {}
    last = dt.datetime.fromisoformat(st['refreshed']) if st.get('refreshed') else None
    if not force and last and (dt.datetime.now() - last).days < 20:
        return
    r = call('GET', 'refresh_access_token', {'grant_type': 'ig_refresh_token'}, base='https://graph.instagram.com')
    if r.get('access_token'):
        with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
            f.write(r['access_token'])
        json.dump({'refreshed': dt.datetime.now().isoformat(timespec='seconds'),
                   'expires_in_days': round(r.get('expires_in', 0) / 86400)}, open(STATE_FILE, 'w', encoding='utf-8'))
        log(f"토큰 연장 완료 (약 {round(r.get('expires_in', 0) / 86400)}일)")


def run():
    try:
        refresh()
    except Exception as e:
        log(f'토큰 연장 실패: {e}')
    q = load_queue()
    now = dt.datetime.now(dt.timezone.utc)
    for item in q:
        if item.get('status') != 'pending':
            continue
        when = dt.datetime.fromisoformat(item['publish_at'])
        if when > now:
            continue
        late_min = int(os.environ.get('LATE_ONLY_MIN', '0') or 0)   # 노트북 안전장치: 클라우드가 놓친(늦은) 것만
        if late_min and (now - when).total_seconds() < late_min * 60:
            continue
        if (now - when).total_seconds() > 6 * 3600:   # 6시간 넘게 지난 건 자동으로 올리지 않음 (PC 꺼져 있던 경우)
            item['status'] = 'missed'
            log(f"[건너뜀] {item['id']} 예정 {item['publish_at']} — 6시간 넘게 지남")
            continue
        try:
            pid, link = publish_carousel(item['media'], item['caption'])
            item.update(status='published', post_id=pid, permalink=link,
                        published_at=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'))
            log(f"[게시] {item['id']} {link}")
        except Exception as e:
            item['status'] = 'error'
            item['error'] = str(e)[:500]
            log(f"[오류] {item['id']} {e}")
        save_queue(q)
    save_queue(q)


def check():
    if MODE == 'facebook':
        me = call('GET', IG_USER, {'fields': 'id,username'})
        print('계정:', me.get('username'), '| id:', me.get('id'), '| 방식: facebook')
    else:
        me = call('GET', 'me', {'fields': 'user_id,username,account_type'})
        print('계정:', me.get('username'), '| 유형:', me.get('account_type'), '| id:', me.get('user_id'))
    try:
        lim = call('GET', f'{IG_USER}/content_publishing_limit', {'fields': 'quota_usage,config'})
        print('게시 한도:', json.dumps(lim.get('data', lim), ensure_ascii=False))
    except Exception as e:
        print('한도 조회 실패(게시엔 영향 없을 수 있음):', e)


def show():
    for it in load_queue():
        print(f"{it['publish_at']}  {it.get('status'):10} {it['id']}  {it.get('permalink', '') or it.get('error', '')[:80]}")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    {'check': check, 'run': run, 'list': show, 'refresh': lambda: refresh(True)}.get(cmd, show)()
