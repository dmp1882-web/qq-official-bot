"""点歌音频流程: 网易云weapi下载(VIP) -> SILK编码 -> QQ语音发送"""
import os, subprocess, uuid, threading, http.server, socketserver, re
import json, base64, random, urllib.request, urllib.parse

AUDIO_DIR = "/tmp/qq_audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

# 启动一个临时 HTTP 服务供 QQ 服务器下载文件
HTTP_PORT = 18080
_httpd = None

def _ensure_httpd():
    global _httpd
    if _httpd:
        return
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=AUDIO_DIR, **kw)
    # 静默日志
    handler.log_message = lambda *a, **kw: None
    _httpd = socketserver.TCPServer(("0.0.0.0", HTTP_PORT), handler)
    t = threading.Thread(target=_httpd.serve_forever, daemon=True)
    t.start()

# ============ 网易云 weapi 加密 ============
_WEAPI_PRESET_KEY = '0CoJUm6Qyw8W8jud'
_WEAPI_IV = '0102030405060708'
_WEAPI_E = '010001'
_WEAPI_N = ('00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7'
            'b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280'
            '104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932'
            '575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b'
            '3ece0462db0a22b8e7')
_COOKIE_FILE = os.environ.get("NETEASE_COOKIE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "netease_cookie.txt"))
_CSRF = '1911be619e21df9e943dd6340d13b8c0'

def _get_cookie():
    try:
        with open(_COOKIE_FILE) as f:
            return f.read().strip()
    except:
        return ''

def _aes_cbc_encrypt_b64(data: bytes, key: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    pad_len = 16 - (len(data) % 16)
    data += bytes([pad_len] * pad_len)
    cipher = Cipher(algorithms.AES(key.encode()), modes.CBC(_WEAPI_IV.encode()), backend=default_backend())
    encryptor = cipher.encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()).decode()

_sec_key_cache = None
def _gen_sec_key():
    global _sec_key_cache
    if _sec_key_cache:
        return _sec_key_cache
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    _sec_key_cache = ''.join(random.choice(chars) for _ in range(16))
    return _sec_key_cache

def _rsa_encrypt(sec_key: str) -> str:
    reversed_key = sec_key[::-1]
    hex_data = reversed_key.encode().hex()
    bi = int(hex_data, 16)
    e = int(_WEAPI_E, 16)
    n = int(_WEAPI_N, 16)
    encrypted = pow(bi, e, n)
    return format(encrypted, 'x').zfill(256)

def _weapi_request(path: str, payload: dict, timeout=15):
    """调用 weapi 接口，返回 dict"""
    sec_key = _gen_sec_key()
    text = json.dumps(payload)
    # 双层 AES: 先用固定key加密，再用随机key加密
    inner = _aes_cbc_encrypt_b64(text.encode(), _WEAPI_PRESET_KEY)
    params = _aes_cbc_encrypt_b64(inner.encode(), sec_key)
    encSecKey = _rsa_encrypt(sec_key)

    cookie = _get_cookie()
    post_data = urllib.parse.urlencode({
        'params': params,
        'encSecKey': encSecKey,
    }).encode()

    req = urllib.request.Request(
        'https://music.163.com' + path + '?csrf_token=' + _CSRF,
        data=post_data,
        headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.90 Safari/537.36',
            'Referer': 'https://music.163.com/',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'MUSIC_U=' + cookie + '; __csrf=' + _CSRF + ';',
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def _search_song_info(song_name: str):
    qs = urllib.parse.urlencode({
        's': song_name, 'type': 1, 'offset': 0, 'total': 'true', 'limit': 1,
    })
    url = 'https://music.163.com/api/search/get/web?csrf_token=&hlpretag=&hlposttag=&' + qs
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    songs = data.get('result', {}).get('songs', [])
    if not songs:
        return None, None, 0
    s = songs[0]
    artist = s['artists'][0]['name'] if s.get('artists') else ''
    dur = 0
    try:
        dur = int(s.get('duration', 0)) // 1000
    except Exception:
        dur = 0
    return s['id'], s['name'] + '-' + artist, dur

def _search_song_id(song_name: str):
    """用公开搜索API拿歌曲ID（无需登录）"""
    sid, desc, _dur = _search_song_info(song_name)
    return sid, desc

def _get_song_url(song_id: int):
    """用 weapi 获取 VIP 歌曲播放链接"""
    result = _weapi_request('/weapi/song/enhance/player/url', {
        'ids': [song_id],
        'br': 320000,
    })
    if result.get('code') != 200:
        return None
    data = result.get('data', [])
    if not data:
        return None
    return data[0].get('url')

def _download_file(url, mp3_path, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with open(mp3_path, "wb") as f:
            f.write(resp.read())
    return os.path.exists(mp3_path) and os.path.getsize(mp3_path) >= 10000

def _encode_to_silk(mp3_path, silk_path):
    from graiax import silkcoder
    silkcoder.encode(mp3_path, silk_path, rate=24000, tencent=True)
    try:
        os.remove(mp3_path)
    except Exception:
        pass
    if os.path.exists(silk_path) and os.path.getsize(silk_path) >= 1000:
        return True
    try:
        if os.path.exists(silk_path):
            os.remove(silk_path)
    except Exception:
        pass
    return False


_BILI_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36", "Referer": "https://www.bilibili.com/"}
def _bili_getj(url):
    req = urllib.request.Request(url, headers=_BILI_H)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())
def _parse_bili_duration(s):
    try:
        parts = [int(x) for x in str(s).strip().split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        pass
    return 0

def _bilibili_audio_url(song_name, expected_duration=0):
    if " - " in song_name:
        singer, song = [x.strip() for x in song_name.split(" - ", 1)]
    elif "-" in song_name:
        singer, song = [x.strip() for x in song_name.split("-", 1)]
    else:
        singer, song = "", song_name.strip()
    bad_words = ["合集", "歌单", "串烧", "全集", "精选", "AI", "人工智能", "翻唱", "cover", "奥特曼", "特摄", "乐评", "盘点", "反应", "reaction", "鬼畜", "混剪", "试听", "预告", "教学", "教程", "伴奏"]
    good_types = ["MV", "音乐现场", "原创音乐", "演奏", "音乐"]
    bad_types = ["特摄", "乐评盘点"]
    candidates = []
    urls = [
        f"https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={urllib.parse.quote(song_name)}&tid=3",
        f"https://api.bilibili.com/x/web-interface/search/all/v2?keyword={urllib.parse.quote(song_name)}",
    ]
    for url in urls:
        try:
            d = _bili_getj(url)
        except Exception:
            continue
        if not d or d.get("code") != 0:
            continue
        data = d.get("data", {})
        videos = []
        if isinstance(data, dict) and "result" in data:
            if isinstance(data["result"], list) and data["result"] and isinstance(data["result"][0], dict) and "bvid" in data["result"][0]:
                videos = data["result"]
            else:
                for item in data["result"]:
                    if isinstance(item, dict) and item.get("result_type") == "video":
                        videos = item.get("data", [])
                        break
        for v in videos[:10]:
            title = re.sub("<[^>]+>", "", v.get("title", "") or "")
            bvid = v.get("bvid")
            if not bvid:
                continue
            dur = _parse_bili_duration(v.get("duration", ""))
            score = 0
            if song and song in title:
                score += 20
            if singer and singer in title:
                score += 10
            typename = v.get("typename", "") or ""
            if typename in good_types:
                score += 5
            if typename in bad_types:
                score -= 50
            for w in bad_words:
                if w in title and w not in song_name:
                    score -= 100
            if dur:
                if dur > 600:
                    score -= 100
                if expected_duration:
                    diff = abs(dur - expected_duration)
                    if diff <= 30:
                        score += 10
                    elif diff <= 60:
                        score += 5
                    elif diff > 120:
                        score -= 20
            candidates.append((score, bvid, title, dur))
        if candidates:
            break
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    score, bvid, title, dur = candidates[0]
    if score < 10:
        return None
    print(f"[bili_pick] score={score} dur={dur} title={title[:40]} bvid={bvid}", flush=True)
    pages = _bili_getj(f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}")
    if not pages.get("data"):
        return None
    cid = pages["data"][0]["cid"]
    d2 = _bili_getj(f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=16&fnval=16")
    audio = d2.get("data", {}).get("dash", {}).get("audio", [])
    if not audio:
        return None
    best = max(audio, key=lambda x: x.get("bandwidth", 0))
    return best.get("baseUrl")

def _bilibili_download_and_encode(song_name, mp3_path, silk_path, timeout=60, expected_duration=0):
    url = _bilibili_audio_url(song_name, expected_duration)
    if not url:
        return False
    m4a_path = mp3_path + ".m4a"
    try:
        req = urllib.request.Request(url, headers=_BILI_H)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(m4a_path, "wb") as f:
                f.write(resp.read())
        if not os.path.exists(m4a_path) or os.path.getsize(m4a_path) < 10000:
            return False
        import subprocess as _sp
        r = _sp.run(["ffmpeg", "-y", "-v", "error", "-i", m4a_path, mp3_path], capture_output=True, timeout=timeout)
        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) < 10000:
            return False
        return _encode_to_silk(mp3_path, silk_path)
    finally:
        for _f in (m4a_path,):
            try:
                if os.path.exists(_f):
                    os.remove(_f)
            except Exception:
                pass

def download_and_encode(song_name, timeout=60):
    fid = uuid.uuid4().hex[:8]
    mp3_path = os.path.join(AUDIO_DIR, fid + ".mp3")
    silk_path = os.path.join(AUDIO_DIR, fid + ".silk")
    try:
        song_id, desc, expected_duration = _search_song_info(song_name)
        if not song_id:
            return None
        try:
            song_url = _get_song_url(song_id)
            if song_url and _download_file(song_url, mp3_path, timeout):
                if _encode_to_silk(mp3_path, silk_path):
                    return silk_path
        except Exception:
            pass
        try:
            if _bilibili_download_and_encode(song_name, mp3_path, silk_path, timeout, expected_duration):
                return silk_path
        except Exception:
            pass
        try:
            meting_url = f"https://api.injahow.cn/meting/?server=netease&type=url&id={song_id}"
            if _download_file(meting_url, mp3_path, timeout):
                if _encode_to_silk(mp3_path, silk_path):
                    return silk_path
        except Exception:
            pass
        return None
    except Exception:
        return None
    finally:
        if os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
            except Exception:
                pass

def get_public_url(silk_path, public_ip):
    _ensure_httpd()
    fname = os.path.basename(silk_path)
    return "http://" + public_ip + ":" + str(HTTP_PORT) + "/" + fname

def cleanup(silk_path):
    try:
        os.remove(silk_path)
    except:
        pass
