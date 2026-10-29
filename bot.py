#!/usr/bin/env python3
"""QQ官方机器人 - 基于 botpy SDK，功能移植自 NapCat 版 bot.py
   艾特1级已砍（官方 API 不给成员等级）；点歌改文字链（无音乐卡片）
"""
import botpy
from botpy.message import GroupMessage
from botpy.http import Route
import asyncio
import json, time, urllib.request, urllib.parse, re, base64, random
import sys
import concurrent.futures
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import song_audio

def log(*a):
    print(time.strftime('[%m-%d %H:%M:%S]'), *a, flush=True)

# ---- 配置 ----
# 所有密钥只通过环境变量或本目录下的 key 文件提供（见 .env.example / README）。
# 仓库里不包含任何真实密钥；未配置的可选功能会自动不可用，不影响其他功能。
def _read_secret(env_name, filename):
    v = os.environ.get(env_name, "").strip()
    if v:
        return v
    p = os.path.join(BASE_DIR, filename)
    if os.path.exists(p):
        return open(p).read().strip()
    return ""

APPID = _read_secret("QQ_APPID", "qq_appid.txt")
SECRET = _read_secret("QQ_APP_SECRET", "qq_app_secret.txt")
DEEPSEEK_KEY_FILE = os.environ.get("DEEPSEEK_KEY_FILE", os.path.join(BASE_DIR, "deepseek_key.txt"))
BAIDU_KEY_FILE = os.environ.get("BAIDU_KEY_FILE", os.path.join(BASE_DIR, "baidu_key.txt"))
# 查玩家（Xbox）需要自建中转服务（见 README），未配置 XBL_RELAY_URL 时该功能不可用
XBL_RELAY = os.environ.get("XBL_RELAY_URL", "").rstrip("/")
XBL_RELAY_SECRET_FILE = os.environ.get("XBL_RELAY_SECRET_FILE", os.path.join(BASE_DIR, "xbl_relay_secret.txt"))
DEEPSEEK_API = os.environ.get("DEEPSEEK_API", "https://api.deepseek.com/v1/chat/completions")
MEMORY_FILE = os.environ.get("MEMORY_FILE", os.path.join(BASE_DIR, "memory.json"))
OPENID_MAP_FILE = os.path.join(BASE_DIR, "openid_qq_map.json")
# 点赞依赖 NapCat（OneBot HTTP API），未配置 NAPCAT_TOKEN 时该功能不可用
NAPCAT_API = os.environ.get("NAPCAT_API", "http://127.0.0.1:3000")
NAPCAT_TOKEN = _read_secret("NAPCAT_TOKEN", "napcat_token.txt")
# 点歌语音的旧 URL 回退方式需要一个公网可达的主机名/IP（file_data 直传失败时才会用到）
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "")
# 查服目标（Minecraft 基岩版服务器），未配置时查服不可用
MC_SERVER_HOST = os.environ.get("MC_SERVER_HOST", "")
MC_SERVER_PORT = int(os.environ.get("MC_SERVER_PORT", "19132"))
MEMORY_MAX_TURNS = 10

SYSTEM_PROMPT = "你是QQ群里的AI助手，群里那个嘴最毒但最懂行的朋友。用简体中文，口语化，简短有力，每条回复控制在3句以内，先直接给答案，再用一句讽刺、吐槽或反问扎一下。语气尖酸刻薄、毒舌，擅长阴阳怪气和冷嘲热讽，话里带刺，但只针对观点、逻辑和行为，不针对身份，不进行人身攻击，不骂脏话，不说教，不道歉，不说'作为AI'。不要长文，不要Markdown加粗、列表等排版，只输出纯文本。"


TRIGGER_KEYWORDS = ["ai", "AI"]

# ---- 记忆 ----
def load_memory():
    try:
        return json.load(open(MEMORY_FILE))
    except Exception:
        return {}

def save_memory(mem):
    try:
        json.dump(mem, open(MEMORY_FILE, 'w'), ensure_ascii=False)
    except Exception as e:
        log("[mem_err]", e)

def get_history(group_id, user_text, speaker=""):
    mem = load_memory()
    hist = mem.get(str(group_id), [])
    msgs = [{"role": "system", "content": SYSTEM_PROMPT + "注意：群聊有多个人，每条消息前面用[名字]标注了发言人。不同的人是不同的个体，不要把A说的话算到B头上。"}]
    for h in hist[-MEMORY_MAX_TURNS:]:
        if len(h) == 3:
            spk, utext, atext = h
            msgs.append({"role": "user", "content": f"[{spk}] {utext}"})
        else:  # 兼容老格式
            msgs.append({"role": "user", "content": h[0]})
        msgs.append({"role": "assistant", "content": h[-1]})
    msgs.append({"role": "user", "content": f"[{speaker}] {user_text}" if speaker else user_text})
    return msgs

def update_memory(group_id, user_text, assistant_text, speaker=""):
    mem = load_memory()
    k = str(group_id)
    hist = mem.get(k, [])
    hist.append([speaker, user_text, assistant_text])
    mem[k] = hist[-MEMORY_MAX_TURNS:]
    save_memory(mem)

# ---- DeepSeek ----
def deepseek_chat(messages, temperature=0.8):
    key = open(DEEPSEEK_KEY_FILE).read().strip()
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "max_tokens": 500,
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(DEEPSEEK_API, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    resp = json.load(urllib.request.urlopen(req, timeout=60))
    return resp["choices"][0]["message"]["content"].strip()

# ---- 搜索 ----
def needs_search(text):
    keywords = ["今天", "现在", "最新", "天气", "新闻", "价格", "多少钱",
                "热搜", "排名", "推荐", "是什么", "什么意思", "解释",
                "介绍一下", "含义"]
    return any(k in text for k in keywords)

def ai_needs_search(text):
    if needs_search(text):
        return True
    try:
        r = deepseek_chat([
            {"role": "user", "content":
             '判断下面这个问题是否需要联网搜索。\n'
             '必须回答"要"的情况：询问某个词/概念/人/事是什么、是什么意思；'
             '涉及2024年之后的新事物、新词、网络用语、游戏更新；'
             '实时信息（天气/价格/新闻/排名）；你有一丝一毫不确定。\n'
             '只有"不要"的情况：打招呼、闲聊、1+1=2这种绝对常识。\n'
             '问题：' + text + '\n只回复一个字："要"或"不要"'}
        ], temperature=0.1)
        return ("要" in r) and ("不要" not in r)
    except Exception as e:
        log("[ai_needs_search_err]", e)
        return False

def baidu_search(query):
    try:
        key = open(BAIDU_KEY_FILE).read().strip()
        data = json.dumps({"messages": [{"role": "user", "content": query}]}).encode()
        req = urllib.request.Request(
            "https://qianfan.baidubce.com/v2/ai_search/web_summary",
            data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key})
        resp = json.load(urllib.request.urlopen(req, timeout=30))
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log("[baidu_search_err]", e)
        return None

def is_mc_item_query(text):
    if len(text) > 8 or len(text) < 2:
        return False
    bad = ["吗", "呢", "怎么", "为什么", "什么", "如何", "?", "？", "是不是",
           "来", "给", "要", "求", "搞", "弄", "做", "个", "些", "点", "下",
           "我", "你", "他", "的", "了", "吧", "啊", "呀", "哦"]
    if any(w in text for w in bad):
        return False
    return True

# ---- 指令 ----
COMMANDS = {
    "查玩家": {"aliases": ["查询玩家", "找玩家", "搜玩家", "查一下玩家", "帮我查玩家"], "keywords": ["玩家"]},
    "查皮肤": {"aliases": ["查询皮肤", "找皮肤", "搜皮肤"], "keywords": ["皮肤"]},
    "查合成": {"aliases": ["查询合成", "合成表查询", "怎么合成"], "keywords": ["合成"]},
    "查生物": {"aliases": ["查询生物", "找生物"], "keywords": ["生物"]},
    "查附魔": {"aliases": ["查询附魔", "找附魔"], "keywords": ["附魔"]},
    "查药水": {"aliases": ["查询药水", "找药水"], "keywords": ["药水"]},
    "查村民": {"aliases": ["查询村民", "找村民"], "keywords": ["村民"]},
    "换算": {"aliases": ["坐标换算", "换算坐标"], "keywords": ["换算", "坐标"]},
    "点歌": {"aliases": ["点一首", "放歌", "来一首", "放一首"], "keywords": ["点歌"]},
    "点赞": {"aliases": ["给我点赞", "求点赞", "帮我点赞"], "keywords": ["点赞"]},
    "搜一下": {"aliases": ["搜索一下", "帮我搜"], "keywords": []},
    "查wiki": {"aliases": ["查询wiki", "查维基", "wiki查询"], "keywords": ["wiki", "维基"]},
    "查服": {"aliases": ["查询服务器", "服务器状态", "查服务器", "服务器查询"], "keywords": ["查服"]},
    "摇一摇": {"aliases": ["摇一摇", "摇一下", "摇摇", "抽签", "摇"], "keywords": ["摇一摇", "抽签"]},
}

def normalize_command(text):
    t = text.strip()
    # 去掉开头的命令前缀 / ! ！
    while t and t[0] in ("/", "!", "！"):
        t = t[1:].strip()
    # 去掉开头的 AI / AIAI 前缀：用户常说 "AI 点歌 xxx"、"AIAI 查服"
    # 不去掉的话，text 不以指令开头，会走 AI 意图兜底，兜底失败就落到
    # 底部的 AI 聊天兜底，造成“用命令时 AI 又蹦出来”
    while True:
        low = t.lower()
        stripped = None
        for prefix in ("aiai", "ai"):
            if not low.startswith(prefix):
                continue
            after = t[len(prefix):]
            if after == "":
                stripped = ""
                break
            # 前缀后紧跟空格/标点，一定是前缀，直接剥
            if after[0] in (" ", "，", ",", "、", "：", ":", "！", "!"):
                stripped = after.strip()
                break
            # 前缀后紧跟指令名，例如 "AI查服"，也剥
            rest = after.strip()
            if any(rest.startswith(c) for c in COMMANDS):
                stripped = rest
                break
            # 前缀后紧跟指令别名，例如 "AI放歌"，也剥
            for info in COMMANDS.values():
                if any(rest.startswith(a) for a in info.get("aliases", [])):
                    stripped = rest
                    break
            if stripped is not None:
                break
        if stripped is None:
            break
        t = stripped
    for cmd, info in COMMANDS.items():
        for alias in sorted(info["aliases"], key=len, reverse=True):
            if t.startswith(alias):
                return cmd + t[len(alias):]
    return t

def ai_classify_intent(text):
    names = "、".join(COMMANDS.keys())
    prompt = (
        "你是QQ群机器人的意图分类器。判断用户这句话是不是想调用机器人指令。"
        "可选指令：" + names + "。\n"
        "用户说：" + text + "\n"
        "只有用户明显想调用指令时才返回对应指令名，否则cmd返回\"无\"。"
        "arg是紧跟指令的具体参数（如玩家名、歌名、坐标），没有则为空字符串。"
        "只返回JSON，不要其他内容：{\"cmd\": \"指令名或无\", \"arg\": \"参数\"}"
    )
    try:
        resp = deepseek_chat([{"role": "user", "content": prompt}])
        m = re.search(r"\{[^{}]*\}", resp)
        if not m:
            return None, None
        d = json.loads(m.group(0))
        cmd = str(d.get("cmd", "无")).strip()
        arg = str(d.get("arg", "")).strip()
        if cmd in COMMANDS and arg:
            return cmd, arg
    except Exception as e:
        log("[intent_err]", e)
    return None, None

# ---- 摇一摇：全站乱抽 B 站视频 ----
# 抽法：两个随机高频汉字拼成关键词 -> B站视频搜索 -> 随机翻一页 -> 随机挑一条。
# 结果池横跨全站各区，抽到啥算啥（实测含音乐、短剧、校园生活等）。
SHAKE_CHARS = "的了是我不在和有人大这上一个为国学到说"
SHAKE_FALLBACK = [
    "点评系统被这视频整沉默了，你自己品吧。",
    "这签抽得很有你的风格：没什么用，但很热闹。",
    "AI点评罢工了。不过这视频配你，挺合适的，别问为什么。",
]


def shake_draw_video():
    H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
         "Referer": "https://www.bilibili.com/"}
    for attempt in range(5):
        kw = random.choice(SHAKE_CHARS) + random.choice(SHAKE_CHARS)
        page = random.randint(1, 15)
        res = []
        try:
            url = ("https://api.bilibili.com/x/web-interface/search/type"
                   "?search_type=video&keyword=" + urllib.parse.quote(kw)
                   + "&page=" + str(page) + "&page_size=20")
            d = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=H), timeout=12))
            res = (d.get("data") or {}).get("result") or []
        except Exception as e:
            log("[shake_try]", attempt, e)
            time.sleep(1)
        if not res:
            continue
        v = random.choice(res)
        bvid = v.get("bvid", "")
        if not bvid:
            continue
        return {
            "title": re.sub(r"<[^>]+>", "", v.get("title") or ""),
            "author": v.get("author", ""),
            "bvid": bvid,
            "play": v.get("play", 0) or 0,
            "typename": v.get("typename", ""),
            "pic": (("https:" + v["pic"]) if (v.get("pic") or "").startswith("//")
                    else (v.get("pic") or "")),
            "url": "https://www.bilibili.com/video/" + bvid,
        }
    return None


SHAKE_BANNED = ["手气", "运气", "彩票"]


def _shake_trim40(r):
    r = (r or "").strip().strip('"').strip("“”").replace(chr(10), " ")
    if len(r) > 40:
        cut = r[:40]
        for p in "。？！":
            idx = cut.rfind(p)
            if idx >= 10:
                cut = cut[:idx + 1]
                break
        r = cut
    return r


def _shake_cover_uri(v):
    pic = v.get("pic") or ""
    if not pic:
        return None
    H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
         "Referer": "https://www.bilibili.com/"}
    data = urllib.request.urlopen(urllib.request.Request(pic, headers=H), timeout=15).read()
    if not data or len(data) < 3000:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def _shake_vision_once(v, uri, extra, max_tokens=1200):
    key = open(DEEPSEEK_KEY_FILE).read().strip()
    _p = v.get("play", 0) or 0
    _ps = ("%.1f万" % (_p / 10000.0)) if _p >= 10000 else str(_p)
    prompt = (
        "你是一位脱口秀编剧，嘴毒但有逻辑，擅长用比喻和反问。禁止脏话。"
        "禁止使用『手气』『运气』『彩票』这几个词。"
        "群里有人用「摇一摇」随机抽到了这个B站视频，图里是它的封面。"
        "请结合封面画面（人物、文字、画风、制作质感）和下面的标题信息，封面上的大字文字要认真读，如果封面文字和视频标题对不上（比如数量、型号、说法不一致），优先拿这个矛盾开涮，"
        "写一句不超过40字的毒舌点评：可以损这个视频本身，"
        "也可以损他偏偏抽到这个视频的品味和眼光；"
        "但绝不能编造他的个人情况（成绩、学校、工作、长相、代码水平等）。"
        "只输出点评正文，不要引号，不要解释。\n"
        "视频标题：《" + (v.get("title") or "") + "》\n"
        "UP主：" + (v.get("author") or "") + "，分区：" + (v.get("typename") or "未知")
        + "，播放量：" + _ps + extra
    )
    body = {"model": "deepseek-flash", "max_tokens": max_tokens, "temperature": 0.8,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": uri}}]}]}
    req = urllib.request.Request(DEEPSEEK_API, data=json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    resp = json.load(urllib.request.urlopen(req, timeout=90))
    return resp["choices"][0]["message"].get("content") or ""


def shake_comment(v):
    # 视觉点评与文字点评并行竞速：视觉25秒内出合格结果就用视觉的，
    # 否则直接用已算好的文字版，避免视觉慢/空返回时群里长时间没反应
    def _vision():
        try:
            uri = _shake_cover_uri(v)
        except Exception as e:
            log("[shake_cover_err]", e)
            return None
        if not uri:
            return None
        try:
            r = _shake_trim40(_shake_vision_once(v, uri, ""))
        except Exception as e:
            log("[shake_vision_err]", e)
            return None
        if not r:
            log("[shake_vision_empty]", 0, v.get("bvid", ""))
            return None
        if any(b in r for b in SHAKE_BANNED):
            log("[shake_vision_banned]", 0, r[:40])
            return None
        log("[shake_vision]", v.get("bvid", ""))
        return r

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        fv = ex.submit(_vision)
        ft = ex.submit(_shake_comment_text, v)
        r = None
        try:
            r = fv.result(timeout=25)
        except Exception:
            r = None
        if r:
            return r
        try:
            return ft.result(timeout=30)
        except Exception:
            return random.choice(SHAKE_FALLBACK)
    finally:
        ex.shutdown(wait=False)


def _shake_comment_text(v):
    play = v["play"]
    play_s = ("%.1f万" % (play / 10000.0)) if play >= 10000 else str(play)
    base = (
        "你是一位脱口秀编剧，嘴毒但有逻辑，擅长用比喻和反问。禁止脏话。"
        "禁止使用『手气』『运气』『彩票』这几个词。"
        "群里有人用「摇一摇」随机抽到了下面这个B站视频，"
        "请写一句不超过40字的毒舌点评：可以损这个视频本身，"
        "也可以损他偏偏抽到这个视频的品味和眼光；"
        "但绝不能编造他的个人情况（成绩、学校、工作、长相、代码水平等）。"
        "风格参考：「这么厉害，怎么还没被写进教科书？」"
        "「你这就是2G网冲浪的速度？」"
        "「大数据时代，算法觉得你缺什么就给你推什么，可惜推送了你也只能干看着。」"
        "只输出点评正文，不要引号，不要解释。\n"
        "视频标题：《" + v["title"] + "》\n"
        "UP主：" + v["author"] + "，分区：" + (v["typename"] or "未知")
        + "，播放量：" + play_s
    )
    extra = ""
    for attempt in range(3):
        try:
            r = deepseek_chat([{"role": "user", "content": base + extra}])
            r = (r or "").strip().strip('"').strip("“”").replace(chr(10), " ")
            if not r:
                continue
            if len(r) > 40:
                cut = r[:40]
                for p in "。？！":
                    idx = cut.rfind(p)
                    if idx >= 10:
                        cut = cut[:idx + 1]
                        break
                r = cut
            if any(b in r for b in SHAKE_BANNED):
                log("[shake_cmt_banned]", attempt, r[:40])
                extra = ("\n注意：上一句用了禁用词或角度太老套，"
                         "必须换一个全新的吐槽角度重写，"
                         "比如算法推荐、标题手法、内容质量、品味眼光。")
                continue
            return r
        except Exception as e:
            log("[shake_cmt_err]", e)
            break
    return random.choice(SHAKE_FALLBACK)


SHAKE_SUMMARY_FALLBACK = [
    "这一把抽完，建议把B站卸载了冷静一下。",
    "这批视频凑一块儿，算法看了都想报警。",
]


def shake_summary(videos):
    lines = []
    for i, v in enumerate(videos, 1):
        _p = v["play"]
        _ps = ("%.1f万" % (_p / 10000.0)) if _p >= 10000 else str(_p)
        lines.append("%d.《%s》分区：%s，播放量：%s"
                     % (i, v["title"], v["typename"] or "未知", _ps))
    base = (
        "你是一位脱口秀编剧，嘴毒但有逻辑，擅长用比喻和反问。禁止脏话。"
        "禁止使用『手气』『运气』『彩票』这几个词。"
        "群里有人一次摇出了下面这几个B站视频，"
        "请结合它们的共同特征（题材、质量、调性），"
        "写一句不超过40字的终极总结毒舌点评，"
        "损这批视频的整体水平和这个人的品味眼光；"
        "绝不能编造他的个人情况（成绩、学校、工作、长相、代码水平等）。"
        "风格参考：「连看三个这种视频，我建议你直接卸载B站去治治脑子。」"
        "只输出点评正文，不要引号，不要解释。\n"
        + "\n".join(lines)
    )
    extra = ""
    for attempt in range(3):
        try:
            r = deepseek_chat([{"role": "user", "content": base + extra}])
            r = (r or "").strip().strip('"').strip("“”").replace(chr(10), " ")
            if not r:
                continue
            if len(r) > 40:
                cut = r[:40]
                for p in "。？！":
                    idx = cut.rfind(p)
                    if idx >= 10:
                        cut = cut[:idx + 1]
                        break
                r = cut
            if any(b in r for b in SHAKE_BANNED):
                log("[shake_sum_banned]", attempt, r[:40])
                extra = ("\n注意：上一句用了禁用词或角度太老套，"
                         "必须换一个全新的角度重写。")
                continue
            return r
        except Exception as e:
            log("[shake_sum_err]", e)
            break
    return random.choice(SHAKE_SUMMARY_FALLBACK)


# ---- 机器人主体 ----
class QQBot(botpy.Client):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._seq = {}
        # msg_id 去重：QQ 官方文档说相同 msg_id 可能重复推送，
        # 需结合 msg_seq 去重。同一 msg_id 在短时间内重复出现即视为重推。
        self._seen_msg_ids = set()
        self._shake_last = {}

    def _next_seq(self, group_openid):
        self._seq[group_openid] = self._seq.get(group_openid, 0) + 1
        return self._seq[group_openid]

    async def send_text(self, group_openid, msg_id, text):
        try:
            await self.api.post_group_message(
                group_openid=group_openid, msg_type=0, content=text,
                msg_id=msg_id, msg_seq=self._next_seq(group_openid))
            log("[sent] ->群", group_openid[:8], ":", text[:50])
        except Exception as e:
            log("[send_fail]", e)

    async def send_image(self, group_openid, msg_id, image_url, caption=""):
        try:
            media = await self.api.post_group_file(
                group_openid=group_openid, file_type=1, url=image_url)
            file_info = media.get("file_info") if isinstance(media, dict) else getattr(media, "file_info", None)
            await self.api.post_group_message(
                group_openid=group_openid, msg_type=7,
                media={"file_info": file_info},
                msg_id=msg_id, msg_seq=self._next_seq(group_openid))
            if caption:
                await self.send_text(group_openid, msg_id, caption)
            log("[sent] ->群", group_openid[:8], "[图片]", caption[:30])
        except Exception as e:
            log("[img_fail]", e)
            if caption:
                await self.send_text(group_openid, msg_id, caption + "（图片发送失败）")

    async def send_voice(self, group_openid, msg_id, silk_url, caption="", silk_path=None):
        try:
            file_info = None
            # 首选 file_data 直传：把 SILK 字节直接传给 QQ，不让 QQ 回连本机取文件。
            # 原 URL 方式要求 QQ 能回连本机 18080 端口取文件，但服务器的公网入口
            # 常被防火墙/云安全组挡住（历次 [voice_fail] 40093007 富媒体下载失败、
            # /files 接口超时全是这个原因），所以默认走 file_data 直传。
            if silk_path:
                try:
                    with open(silk_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=group_openid)
                    media = await self.api._http.request(route, json={"file_type": 3, "file_data": b64, "srv_send_msg": False})
                    file_info = media.get("file_info") if isinstance(media, dict) else getattr(media, "file_info", None)
                    if file_info:
                        log("[voice] file_data 直传成功")
                except Exception as e:
                    log("[voice_data_fail]", e)
            if not file_info:
                # 回退老的 URL 方式（万一以后安全组放行了 18080 还能用）
                media = await self.api.post_group_file(
                    group_openid=group_openid, file_type=3, url=silk_url)
                file_info = media.get("file_info") if isinstance(media, dict) else getattr(media, "file_info", None)
            await self.api.post_group_message(
                group_openid=group_openid, msg_type=7,
                media={"file_info": file_info},
                msg_id=msg_id, msg_seq=self._next_seq(group_openid))
            if caption:
                await self.send_text(group_openid, msg_id, caption)
            log("[sent] ->群", group_openid[:8], "[语音]", caption[:30])
            return True
        except Exception as e:
            log("[voice_fail]", e)
            return False

    async def on_group_at_message_create(self, message: GroupMessage):
        group_openid = message.group_openid
        msg_id = message.id
        _au = getattr(message, "author", None)
        sender = getattr(_au, "member_nick", None) or getattr(_au, "username", "?")
        member_openid = getattr(_au, "member_openid", "") or ""
        # 去掉 @ 机器人的标记
        text = re.sub(r"<@!?\d+>", "", message.content or "").strip()
        text = re.sub(r"^@\S+\s*", "", text).strip()
        log("[recv] 群", group_openid[:8], sender, ":", text[:60], "msg_seq=", getattr(message, "msg_seq", None))
        # 去重：同一 msg_id 已处理过则直接丢弃，不再跑指令、不再发消息
        if msg_id in self._seen_msg_ids:
            log("[dup] 跳过重复消息", msg_id, "msg_seq=", getattr(message, "msg_seq", None))
            return
        self._seen_msg_ids.add(msg_id)
        # 只保留最近 1000 条，避免集合无限增长
        if len(self._seen_msg_ids) > 1000:
            self._seen_msg_ids = set(list(self._seen_msg_ids)[-1000:])
        try:
            await self.handle_text(group_openid, msg_id, text, sender, member_openid)
        except Exception as e:
            log("[error]", e)
            await self.send_text(group_openid, msg_id, "我卡了一下，重说一遍？")

    async def handle_text(self, group_openid, msg_id, text, sender, member_openid=""):
        text = normalize_command(text)

        # AI意图兜底
        if not any(text.startswith(c) for c in COMMANDS):
            kws = set()
            for info in COMMANDS.values():
                kws.update(info["keywords"])
            if any(k in text for k in kws) and not needs_search(text):
                cmd, arg = ai_classify_intent(text)
                if cmd:
                    log("[intent]", text[:30], "->", cmd, arg[:20])
                    text = "%s %s" % (cmd, arg)

        # 查皮肤
        if text.startswith("查皮肤"):
            name = text.replace("查皮肤", "").strip()
            if name:
                url = f"https://minotar.net/armor/body/{name}/100.png"
                await self.send_image(group_openid, msg_id, url, f"{name} 的皮肤")
            else:
                await self.send_text(group_openid, msg_id, "用法：查皮肤 <用户名>")
            return

        # 查玩家
        if text.startswith("查玩家"):
            name = text.replace("查玩家", "").strip()
            if name:
                try:
                    secret = open(XBL_RELAY_SECRET_FILE).read().strip()
                    req = urllib.request.Request(
                        f"{XBL_RELAY}/v2/search/{urllib.parse.quote(name)}",
                        headers={"X-Relay-Secret": secret, "User-Agent": "qq-official-bot"})
                    d = json.load(urllib.request.urlopen(req, timeout=15))
                    people = d.get("content", {}).get("people", [])
                    if people:
                        p = people[0]
                        gt = p.get("gamertag", name)
                        msg = f"代号：{gt}\nXUID：{p.get('xuid', '')}\n成就分：{p.get('gamerScore', '')}"
                        pic = p.get("displayPicRaw", "")
                        if pic:
                            await self.send_image(group_openid, msg_id, pic, msg)
                        else:
                            await self.send_text(group_openid, msg_id, msg)
                    else:
                        await self.send_text(group_openid, msg_id, f"没查到 {name}")
                except Exception as e:
                    log("[xbl_err]", e)
                    await self.send_text(group_openid, msg_id, "查询失败")
            else:
                await self.send_text(group_openid, msg_id, "用法：查玩家 <gamertag>")
            return

        # MC 查询系列
        mc_cmds = {
            "查合成": "Minecraft合成：%s，简短说明材料和摆法，纯文本。如果这个物品在原版中不存在或无法合成，直接说不存在，严禁编造配方。",
            "查生物": "Minecraft生物：%s，简短说明血量伤害掉落，纯文本。如果这个生物在原版中不存在，直接说不存在，严禁编造。",
            "查附魔": "Minecraft附魔：%s，简短说明最高等级效果冲突，纯文本。如果这个附魔在原版中不存在，直接说不存在，严禁编造。",
            "查药水": "Minecraft药水：%s，简短说明酿造配方和效果，纯文本。如果这种药水在原版中不存在或无法酿造，直接说不存在，严禁编造配方。",
            "查村民": "Minecraft村民职业：%s，简短说明交易物品和工作站点，纯文本。如果这个职业在原版中不存在，直接说不存在，严禁编造。",
        }
        for cmd, tpl in mc_cmds.items():
            if text.startswith(cmd):
                name = text.replace(cmd, "").strip()
                if name:
                    r = deepseek_chat([{"role": "user", "content": tpl % name}])
                    await self.send_text(group_openid, msg_id, r)
                else:
                    await self.send_text(group_openid, msg_id, f"用法：{cmd} <名称>")
                return

        # 换算
        if text.startswith("换算"):
            parts = text.replace("换算", "").strip().split()
            if len(parts) >= 3:
                try:
                    x, y, z = int(parts[0]), int(parts[1]), int(parts[2])
                    await self.send_text(
                        group_openid, msg_id,
                        f"主世界 ({x}, {y}, {z})\n下界对应 ({x//8}, {y}, {z//8})")
                except Exception:
                    await self.send_text(group_openid, msg_id, "用法：换算 <x> <y> <z>")
            else:
                await self.send_text(group_openid, msg_id, "用法：换算 <x> <y> <z>（主世界坐标转下界）")
            return

        # 摇一摇（全站乱抽B站视频 + AI毒舌点评，可带数字摇多个，每群10秒冷却）
        if text.startswith("摇一摇"):
            now = time.time()
            last = self._shake_last.get(group_openid, 0)
            if now - last < 10:
                await self.send_text(
                    group_openid, msg_id,
                    "刚摇完，签筒还没凉，%d秒后再摇" % int(10 - (now - last)))
                return
            self._shake_last[group_openid] = now
            _arg = text.replace("摇一摇", "").strip()
            _m = re.search(r"\d+", _arg)
            if _m:
                count = int(_m.group())
            else:
                count = 1
                for _cn, _n in (("五", 5), ("四", 4), ("三", 3), ("二", 2), ("两", 2), ("一", 1)):
                    if _cn in _arg:
                        count = _n
                        break
            clamped = count > 5
            count = max(1, min(5, count))
            videos = []
            seen_bv = set()
            tries = 0
            while len(videos) < count and tries < count + 4:
                tries += 1
                try:
                    v = shake_draw_video()
                except Exception as e:
                    log("[shake_err]", e)
                    v = None
                if not v or v["bvid"] in seen_bv:
                    continue
                seen_bv.add(v["bvid"])
                videos.append(v)
            if not videos:
                await self.send_text(group_openid, msg_id,
                                     "摇空了，签筒里啥也没有，再摇一次试试")
                return
            # 多个视频合并成一条消息发，禁止连发刷屏
            lines = ["🎲 摇一摇" + ((" ×%d" % len(videos)) if len(videos) > 1 else "") + "，你抽到了："]
            if len(videos) > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(videos)) as _ex:
                    _cmts = list(_ex.map(shake_comment, videos))
            else:
                _cmts = [shake_comment(videos[0])]
            for i, v in enumerate(videos, 1):
                _play = v["play"]
                _play_s = ("%.1f万" % (_play / 10000.0)) if _play >= 10000 else str(_play)
                log("[shake]", v["bvid"], v["title"][:30])
                _cmt = _cmts[i - 1]
                if len(videos) > 1:
                    lines.append("%d. 《%s》UP主：%s · 播放 %s" % (i, v["title"], v["author"], _play_s))
                else:
                    lines.append("《%s》" % v["title"])
                    lines.append("UP主：%s · 播放 %s" % (v["author"], _play_s))
                lines.append(v["url"])
                lines.append("毒舌点评：" + _cmt)
            if len(videos) > 1:
                lines.append("🎯 终极总结：" + shake_summary(videos))
            if clamped:
                lines.append("（一次最多摇5个，已按5个给你摇了）")
            await self.send_text(group_openid, msg_id, "\n".join(lines))
            return

        # 点歌（AI匹配歌名 -> YouTube抓音频 -> SILK语音发送）
        if text.startswith("点歌"):
            name = text.replace("点歌", "").strip()
            if name:
                try:
                    # AI 先把模糊描述规范成标准歌名
                    try:
                        match_prompt = f"用户想点歌，他说的是：'{name}'。只返回最匹配的'歌手 - 歌名'，不要任何解释。如果无法判断，一字不差地返回：{name}"
                        matched = deepseek_chat([{"role": "user", "content": match_prompt}]).strip()
                        matched = re.sub(r"^(歌手\s*-\s*歌名|歌手|歌名)\s*[:：]\s*", "", matched)
                        # 防 AI 抽风：短回复直接信任（很可能是歌名本身，如"抱歉"）
                        # 长回复才检查是否为解释性文字
                        bad_words = ["无法判断", "请提供", "不知道", "不清楚", "抱歉，", "对不起，", "？", "?"]
                        if matched and len(matched) < 10:
                            search_name = matched
                        elif matched and len(matched) < 40 and not any(w in matched for w in bad_words):
                            search_name = matched
                        else:
                            search_name = name
                        log("[music] AI匹配", name, "->", search_name)
                    except Exception:
                        search_name = name
                    await self.send_text(group_openid, msg_id, f"正在找《{search_name}》，稍等...")
                    silk_path = await asyncio.to_thread(song_audio.download_and_encode, search_name)
                    if silk_path:
                        url = song_audio.get_public_url(silk_path, PUBLIC_HOST)
                        ok = await self.send_voice(group_openid, msg_id, url, f"🎵《{search_name}》", silk_path=silk_path)
                        song_audio.cleanup(silk_path)
                        if not ok:
                            await self.send_text(group_openid, msg_id, f"语音发送失败，换首试试")
                        log("[music] 点歌", search_name)
                    else:
                        await self.send_text(group_openid, msg_id, f"没找到《{search_name}》，换首试试")
                except Exception as e:
                    log("[music_err]", e)
                    await self.send_text(group_openid, msg_id, "点歌失败，换首试试")
            else:
                await self.send_text(group_openid, msg_id, "用法：点歌 <歌名>")
            return
        # 绑定QQ号：@bot 绑定 12345678
        if text.startswith("绑定") or text.startswith("/绑定"):
            import re as _re2, json as _js2, os as _os2
            _m = _re2.search(r'(\d{5,11})', text)
            if _m:
                _qq = _m.group(1)
                _mapf = OPENID_MAP_FILE
                _map = {}
                if _os2.path.exists(_mapf):
                    try:
                        _map = _js2.load(open(_mapf))
                    except:
                        pass
                _mo = member_openid
                if _mo:
                    _map[_mo] = _qq
                    _js2.dump(_map, open(_mapf, 'w'))
                    await self.send_text(group_openid, msg_id, "绑定成功！以后点面板的点赞就行了")
                else:
                    await self.send_text(group_openid, msg_id, "绑定失败，没拿到你的ID")
            else:
                await self.send_text(group_openid, msg_id, "用法：@bot 绑定 你的QQ号")
            return
        # 点赞：查绑定表拿QQ号，走NapCat点赞
        if text in ("点赞", "给我点赞", "求点赞", "帮我点赞", "点赞我"):
            try:
                def _do_like2():
                    import json as _j, urllib.request as _ur, os as _o
                    _mapf = OPENID_MAP_FILE
                    if not _o.path.exists(_mapf):
                        return "你还没绑定QQ号，先 @bot 绑定 你的QQ号"
                    _map = _j.load(open(_mapf))
                    _mo = member_openid
                    _qq = _map.get(_mo)
                    if not _qq:
                        return "你还没绑定QQ号，先 @bot 绑定 你的QQ号"
                    _tok = NAPCAT_TOKEN
                    _base = NAPCAT_API
                    _h = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + _tok}
                    _req = _ur.Request(_base + '/send_like',
                        data=_j.dumps({"user_id": int(_qq), "times": 10}).encode(), headers=_h)
                    _ur.urlopen(_req, timeout=10).read()
                    return "已给你点赞！"
                result = await asyncio.to_thread(_do_like2)
                await self.send_text(group_openid, msg_id, result)
            except Exception as e:
                log("[like-err]", str(e)[:80])
                await self.send_text(group_openid, msg_id, "点赞失败，稍后再试")
            return


        # 搜一下（显式强制搜索）
        if text.startswith("搜一下"):
            q = text.replace("搜一下", "").strip()
            if q:
                sr = baidu_search(q)
                if sr:
                    reply = deepseek_chat(
                        [{"role": "user", "content":
                          sr[:1200] + "\n" + q +
                          "\n根据搜索结果简短口语化回答，纯文本。"
                          "搜索结果即答案依据，严禁回复\"不知道/查不到/无法联网\"。"}],
                        temperature=0.2)
                else:
                    reply = "搜索失败了，稍后再试吧"
                await self.send_text(group_openid, msg_id, reply)
                update_memory(group_openid, q, reply)
            else:
                await self.send_text(group_openid, msg_id, "用法：搜一下 <内容>")
            return

        # 查wiki：联网搜 Minecraft Wiki 再让 AI 总结，温度高一点
        if text.startswith("查wiki"):
            q = text.replace("查wiki", "").strip()
            if q:
                sr = baidu_search("Minecraft Wiki " + q)
                if sr:
                    reply = deepseek_chat(
                        [{"role": "user", "content":
                          "关于 Minecraft " + q + " 的 Wiki 搜索结果：\n" +
                          sr[:1500] + "\n请用口语化、简短有趣的方式总结一下，纯文本。如果搜索结果里根本没有这个东西，直接说没找到，严禁编造。如果涉及不同版本差异（如老版本合成方式不同），顺带提一句。"}],
                        temperature=0.7)
                else:
                    reply = "没搜到相关 Wiki 内容，换个词试试"
                await self.send_text(group_openid, msg_id, reply)
                update_memory(group_openid, q, reply)
            else:
                await self.send_text(group_openid, msg_id, "用法：查wiki <内容>")
            return

        # 查服：查询MC服务器状态
        if text.startswith("查服") or text.startswith("/查服"):
            try:
                import subprocess
                result = subprocess.run(
                    ["node", os.path.join(BASE_DIR, "mc_ping.js"), MC_SERVER_HOST, str(MC_SERVER_PORT)],
                    capture_output=True, text=True, timeout=20
                )
                import json as _json
                d = _json.loads(result.stdout.strip())
                if "error" in d:
                    await self.send_text(group_openid, msg_id, "查服失败：服务器没响应")
                else:
                    ver = d.get("version", "?")
                    online = d.get("playersOnline", "?")
                    maxp = d.get("playersMax", "?")
                    msg = f"版本：{ver}\n在线：{online}/{maxp}"
                    try:
                        import os as _o2
                        _pf = '/tmp/mc_online_players.json'
                        if _o2.path.exists(_pf):
                            _pl = _json.load(open(_pf))
                            if _pl:
                                msg += "\n玩家：" + "、".join(_pl)
                            else:
                                msg += "\n玩家：无"
                    except:
                        pass
                    await self.send_text(group_openid, msg_id, msg)
                log("[chafu] ok")
            except Exception as e:
                log("[chafu_err]", e)
                await self.send_text(group_openid, msg_id, "查服失败，稍后再试")
            return

        # AI 聊天兜底：走到这里说明前面没有命中任何指令。
        # 防御性检查：如果 clean 仍以指令开头，说明前面漏了 return，
        # 绝不能再发一条 AI 聊天，否则命令和 AI 会同时蹦出来。
        _clean_check = text.strip()
        if any(_clean_check.startswith(c) for c in COMMANDS):
            return
        clean = text
        for k in TRIGGER_KEYWORDS:
            clean = clean.replace(k, "")
        clean = clean.strip()
        if not clean:
            return
        t0 = time.time()
        if ai_needs_search(clean):
            sr = baidu_search(clean)
            if sr:
                reply = deepseek_chat(
                    [{"role": "user", "content":
                      sr[:1200] + "\n" + clean +
                      "\n根据搜索结果简短口语化回答，纯文本。"
                      "搜索结果即答案依据，严禁回复\"不知道/查不到/无法联网\"。"}],
                    temperature=0.2)
            else:
                reply = "搜索失败了，稍后再试吧"
            await self.send_text(group_openid, msg_id, reply)
            update_memory(group_openid, clean, reply)
            return
        if is_mc_item_query(clean):
            r = deepseek_chat([{"role": "user", "content":
                "Minecraft中的\"" + clean + "\"是什么？如果是物品给合成表，"
                "如果是生物给血量掉落，如果是附魔给等级效果，如果是方块给获取方式。"
                "简短纯文本。如果不是MC相关，回复\"不是MC\"。"}])
            if "不是MC" not in r:
                await self.send_text(group_openid, msg_id, r)
                update_memory(group_openid, clean, r, sender)
                return
        msgs = get_history(group_openid, clean, sender)
        reply = deepseek_chat(msgs)
        update_memory(group_openid, clean, reply, sender)
        log("[timing] deepseek %.1fs" % (time.time() - t0))
        await self.send_text(group_openid, msg_id, reply)


if __name__ == "__main__":
    intents = botpy.Intents(public_messages=True)
    client = QQBot(intents=intents)
    log("QQ官方机器人启动中，AppID", APPID)
    client.run(appid=APPID, secret=SECRET)
