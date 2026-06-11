import os
import json
import base64
import tempfile
import streamlit as st
import streamlit.components.v1 as components
import yt_dlp

VERSION = 7

st.set_page_config(page_title="Stream Player", page_icon="🎵", layout="centered")

CACHE_DIR = os.path.join(tempfile.gettempdir(), "stream_player_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

COOKIE_PATH = os.path.join(tempfile.gettempdir(), "stream_player_cookies.txt")


def resolve_cookies():
    if st.session_state.get("uploaded_cookie_path"):
        return st.session_state.uploaded_cookie_path
    try:
        secret = st.secrets.get("YT_COOKIES", "")
    except Exception:
        secret = ""
    if secret:
        with open(COOKIE_PATH, "w") as f:
            f.write(secret)
        return COOKIE_PATH
    return None


def get_po_token():
    try:
        return st.secrets.get("PO_TOKEN", ""), st.secrets.get("VISITOR_DATA", "")
    except Exception:
        return "", ""


CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
    IMPERSONATE = ImpersonateTarget("chrome")
except Exception:
    IMPERSONATE = None


def with_cookies(opts, extra=None, use_cookies=True):
    o = dict(opts)
    c = resolve_cookies() if use_cookies else None
    if c:
        o["cookiefile"] = c
    o["http_headers"] = {
        "User-Agent": CHROME_UA,
        "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.8",
    }
    o["geo_bypass"] = True
    o["geo_bypass_country"] = "HR"
    try:
        proxy = st.secrets.get("PROXY_URL", "")
    except Exception:
        proxy = ""
    if proxy:
        o["proxy"] = proxy
    if IMPERSONATE:
        o["impersonate"] = IMPERSONATE
    if extra:
        o.update(extra)
    return o


BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": False,
    "extract_flat": "in_playlist",
    "skip_download": True,
}

DL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "format_sort": ["abr", "asr"],
    "noplaylist": True,
    "ignore_no_formats_error": False,
    "outtmpl": os.path.join(CACHE_DIR, "%(id)s.%(ext)s"),
}


def best_thumb(entry):
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        return sorted(thumbs, key=lambda t: t.get("width") or 0)[-1].get("url")
    vid = entry.get("id")
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None


def to_track(entry):
    return {
        "title": entry.get("title") or entry.get("id"),
        "id": entry.get("id"),
        "artist": entry.get("uploader") or entry.get("channel") or "",
        "thumb": best_thumb(entry),
    }


def get_entries(url):
    with yt_dlp.YoutubeDL(with_cookies(BASE_OPTS)) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist" or "entries" in info:
        return [to_track(e) for e in (info.get("entries") or []) if e]
    return [to_track(info)]


def search_music(query, limit=10):
    url = f"https://music.youtube.com/search?q={query}#songs"
    opts = with_cookies(BASE_OPTS, {"playlist_items": f"1:{limit}"})
    info = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        pass
    if info and info.get("entries"):
        return [to_track(e) for e in info["entries"] if e][:limit]
    with yt_dlp.YoutubeDL(with_cookies(BASE_OPTS)) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    return [to_track(e) for e in (info.get("entries") or []) if e]


def _find_cached(video_id):
    for f in os.listdir(CACHE_DIR):
        if f.startswith(video_id + ".") and not f.endswith(".json"):
            return os.path.join(CACHE_DIR, f)
    return None


def _build_attempts(video_id):
    """
    Build ordered list of (url, extra_opts, use_cookies) attempts.
    Uses only client names valid in yt-dlp 2026: android_vr, web_safari,
    tv_downgraded, web_creator, web.
    """
    mu = f"https://music.youtube.com/watch?v={video_id}"
    wu = f"https://www.youtube.com/watch?v={video_id}"

    po_token, visitor_data = get_po_token()

    def client(name, po=False):
        args = {"extractor_args": {"youtube": {"player_client": [name]}}}
        if po and po_token and visitor_data:
            args["extractor_args"]["youtube"]["po_token"] = [
                f"web+{po_token}"
            ]
            args["extractor_args"]["youtube"]["visitor_data"] = [visitor_data]
        return args

    base = {"format": "bestaudio[ext=m4a]/bestaudio/best"}

    attempts = [
        # android_vr: no PO token needed, JS-less, most reliable on server IPs
        (wu, {**client("android_vr"), **base}, True),
        (wu, {**client("android_vr"), **base}, False),   # retry without cookies
        # web_safari: good fallback, no PO token needed
        (wu, {**client("web_safari"), **base}, True),
        (wu, {**client("web_safari"), **base}, False),
        # web: standard web client
        (wu, {**client("web"), **base}, False),
        # tv_downgraded: authed client, works well with cookies
        (wu, {**client("tv_downgraded"), **base}, True),
        (wu, {**client("tv_downgraded"), **base}, False),
        # web_creator: may bypass age restriction, needs PO token ideally
        (wu, {**client("web_creator", po=True), **base}, True),
        (wu, {**client("web_creator", po=True), **base}, False),
        # last resort: no client hint, let yt-dlp decide
        (wu, base, False),
    ]
    return attempts


@st.cache_data(show_spinner=False, max_entries=20)
def fetch_audio(video_id):
    """Download audio. Returns (path, meta dict)."""
    meta_path = os.path.join(CACHE_DIR, video_id + ".json")
    cached = _find_cached(video_id)
    if cached and os.path.exists(meta_path):
        with open(meta_path) as f:
            return cached, json.load(f)

    attempts = _build_attempts(video_id)
    info, last_err = None, None
    errors = []

    for i, (u, extra, use_ck) in enumerate(attempts):
        client_hint = (extra.get("extractor_args", {})
                            .get("youtube", {})
                            .get("player_client", ["?"])[0])
        try:
            opts = with_cookies(DL_OPTS, extra, use_ck)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(u, download=True)
            break
        except Exception as e:
            last_err = e
            ck_label = "ck" if use_ck else "no-ck"
            errors.append(f"[{i+1}] {client_hint} ({ck_label}): {str(e)[:120]}")

    if info is None:
        detail = "\n".join(errors)
        raise RuntimeError(
            f"All {len(attempts)} attempts failed.\n\n{detail}\n\nLast error: {last_err}"
        )

    path = _find_cached(video_id)
    if not path:
        raise RuntimeError("Download succeeded but file not found in cache")

    meta = {
        "abr": info.get("abr"),
        "asr": info.get("asr"),
        "acodec": info.get("acodec"),
        "ext": info.get("ext"),
        "format_id": info.get("format_id"),
        "format_note": info.get("format_note"),
        "duration": info.get("duration"),
        "filesize": os.path.getsize(path),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    return path, meta


def render_player(path, meta, autonext):
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    mime = {
        "m4a": "audio/mp4", "webm": "audio/webm", "opus": "audio/ogg",
        "mp3": "audio/mpeg", "ogg": "audio/ogg",
    }.get(ext, "audio/mp4")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    html = """
<div style="font-family:sans-serif;color:#eee;background:transparent">
  <audio id="au" controls autoplay style="width:100%%;margin-bottom:6px"
         src="data:%MIME%;base64,%B64%"></audio>
  <div style="margin:4px 0 8px 0;font-size:13px">
    <label style="margin-right:16px"><input type="checkbox" id="cb_wave" checked> Waveform</label>
    <label><input type="checkbox" id="cb_osc" checked> Oscilloscope</label>
  </div>
  <canvas id="wave" width="900" height="90"
          style="width:100%%;height:90px;background:#111;border-radius:6px;display:block;margin-bottom:6px"></canvas>
  <canvas id="osc" width="900" height="90"
          style="width:100%%;height:90px;background:#111;border-radius:6px;display:block"></canvas>
</div>
<script>
const au = document.getElementById('au');
const waveC = document.getElementById('wave'), oscC = document.getElementById('osc');
const wctx = waveC.getContext('2d'), octx = oscC.getContext('2d');
const cbW = document.getElementById('cb_wave'), cbO = document.getElementById('cb_osc');
let actx = null, analyser = null, peaks = null;

function setup() {
  if (actx) return;
  actx = new (window.AudioContext || window.webkitAudioContext)();
  const src = actx.createMediaElementSource(au);
  analyser = actx.createAnalyser();
  analyser.fftSize = 2048;
  src.connect(analyser);
  analyser.connect(actx.destination);
  fetch(au.src).then(r => r.arrayBuffer()).then(b => actx.decodeAudioData(b)).then(buf => {
    const data = buf.getChannelData(0);
    const n = waveC.width, step = Math.floor(data.length / n);
    peaks = [];
    for (let i = 0; i < n; i++) {
      let max = 0;
      for (let j = 0; j < step; j += 16) {
        const v = Math.abs(data[i * step + j] || 0);
        if (v > max) max = v;
      }
      peaks.push(max);
    }
  }).catch(()=>{});
}

function drawWave() {
  wctx.clearRect(0,0,waveC.width,waveC.height);
  if (!cbW.checked) return;
  if (!peaks) return;
  const h = waveC.height, mid = h/2;
  const prog = au.duration ? au.currentTime / au.duration : 0;
  for (let i = 0; i < peaks.length; i++) {
    const ph = Math.max(2, peaks[i] * (h - 8));
    wctx.fillStyle = (i / peaks.length) <= prog ? '#e05a00' : '#444';
    wctx.fillRect(i, mid - ph/2, 1, ph);
  }
}

function drawOsc() {
  octx.clearRect(0,0,oscC.width,oscC.height);
  if (!cbO.checked || !analyser) return;
  const arr = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(arr);
  octx.strokeStyle = '#e05a00';
  octx.lineWidth = 2;
  octx.beginPath();
  const sw = oscC.width / arr.length;
  for (let i = 0; i < arr.length; i++) {
    const y = (arr[i] / 255) * oscC.height;
    if (i === 0) octx.moveTo(0, y); else octx.lineTo(i * sw, y);
  }
  octx.stroke();
}

function loop() { drawWave(); drawOsc(); requestAnimationFrame(loop); }
loop();

au.addEventListener('play', () => { setup(); if (actx) actx.resume(); });
waveC.addEventListener('click', (e) => {
  if (!au.duration) return;
  const r = waveC.getBoundingClientRect();
  au.currentTime = ((e.clientX - r.left) / r.width) * au.duration;
});

const AUTONEXT = %AUTONEXT%;
au.addEventListener('ended', () => {
  if (!AUTONEXT) return;
  const btns = window.parent.document.querySelectorAll('button');
  for (const b of btns) {
    if (b.innerText.trim() === 'Next' && !b.disabled) { b.click(); break; }
  }
});
</script>
"""
    html = (html.replace("%MIME%", mime)
                .replace("%B64%", b64)
                .replace("%AUTONEXT%", "true" if autonext else "false"))
    components.html(html, height=300)


if "queue" not in st.session_state:
    st.session_state.queue = []
if "current" not in st.session_state:
    st.session_state.current = None

st.caption(f"v{VERSION}")

# ---------------- PLAYER ON TOP ----------------
if st.session_state.current is not None and st.session_state.queue:
    idx = st.session_state.current
    tr = st.session_state.queue[idx]

    art, info_col = st.columns([1, 2])
    with art:
        if tr["thumb"]:
            st.image(tr["thumb"], use_container_width=True)
    with info_col:
        st.subheader(tr["title"])
        if tr["artist"]:
            st.write(tr["artist"])
        st.toggle("Play all (continue to next track)", key="play_all", value=True)

    has_next = idx < len(st.session_state.queue) - 1
    with st.spinner("Fetching audio…"):
        try:
            path, meta = fetch_audio(tr["id"])
            render_player(path, meta, st.session_state.get("play_all", True) and has_next)

            size = meta.get("filesize") or os.path.getsize(path)
            dur = meta.get("duration") or 0
            abr = meta.get("abr")
            real_kbps = (size * 8 / 1000 / dur) if dur else None
            parts = []
            parts.append(f"declared bitrate {abr:.0f} kbps" if abr else "declared bitrate unknown")
            if real_kbps:
                parts.append(f"real data rate {real_kbps:.0f} kbps")
            if meta.get("asr"):
                parts.append(f"sample rate {meta['asr']} Hz")
            parts.append(f"codec {meta.get('acodec') or '?'}")
            parts.append(f"container {meta.get('ext') or '?'}")
            parts.append(f"size {size / (1024*1024):.2f} MB")
            if dur:
                parts.append(f"duration {int(dur // 60)}:{int(dur % 60):02d}")
            fid = meta.get("format_id")
            note = meta.get("format_note")
            parts.append(f"format {fid}{' (' + note + ')' if note else ''}")
            st.caption(" | ".join(parts))
        except Exception as e:
            err_text = str(e)
            st.error(f"Playback failed")
            with st.expander("Show error detail"):
                st.code(err_text)
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={tr['id']}&format=json",
                    headers={"User-Agent": CHROME_UA})
                urllib.request.urlopen(req, timeout=8)
                st.warning(
                    "Probe: this video EXISTS from the server region. "
                    "The block is client, cookie or bot-detection related. "
                    "Upload fresh cookies in Setup, or add PO_TOKEN + VISITOR_DATA secrets."
                )
            except Exception:
                st.warning(
                    "Probe: this video is NOT publicly reachable from the server region (US). "
                    "It may be deleted, private, or geo-locked. "
                    "A proxy in PROXY_URL secret is the only fix for geo locks."
                )
            if st.session_state.get("play_all", True) and has_next:
                if st.button("Skip to next"):
                    st.session_state.current = idx + 1
                    st.rerun()

    p, n = st.columns(2)
    if p.button("Previous", disabled=idx <= 0):
        st.session_state.current = idx - 1
        st.rerun()
    if n.button("Next", disabled=not has_next):
        st.session_state.current = idx + 1
        st.rerun()
else:
    st.info("Search for an artist or paste a link to start listening.")

st.divider()

# ---------------- SEARCH, LINK, SETUP ----------------
tab_search, tab_link, tab_setup = st.tabs(["Search", "Paste link", "Setup"])

with tab_search:
    with st.form("search_form", clear_on_submit=False, border=False):
        q = st.text_input("Artist or song", placeholder="Type and press Enter")
        submitted = st.form_submit_button("Search")
    if submitted and q.strip():
        with st.spinner("Searching YouTube Music…"):
            try:
                st.session_state.search_results = search_music(q.strip())
            except Exception as e:
                st.error(f"Search failed: {e}")
    for i, tr in enumerate(st.session_state.get("search_results", [])):
        c0, c1 = st.columns([1, 5])
        if tr["thumb"]:
            c0.image(tr["thumb"], width=64)
        label = tr["title"] + (f"  ·  {tr['artist']}" if tr["artist"] else "")
        if c1.button(label, key=f"sr{i}", use_container_width=True):
            st.session_state.queue = st.session_state.search_results
            st.session_state.current = i
            st.rerun()

with tab_link:
    with st.form("link_form", clear_on_submit=False, border=False):
        url = st.text_input(
            "YouTube Music link (song or playlist)",
            placeholder="Paste and press Enter",
        )
        loaded = st.form_submit_button("Load")
    if loaded and url.strip():
        with st.spinner("Reading link…"):
            try:
                st.session_state.queue = get_entries(url.strip())
                st.session_state.current = 0 if st.session_state.queue else None
                st.rerun()
            except Exception as e:
                st.error(f"Could not read this link: {e}")

with tab_setup:
    st.write("Cookies")
    up = st.file_uploader("Upload cookies.txt (Netscape format)", type=["txt"])
    if up is not None:
        path = os.path.join(tempfile.gettempdir(), "uploaded_cookies.txt")
        with open(path, "wb") as f:
            f.write(up.getvalue())
        st.session_state.uploaded_cookie_path = path
        st.success("Cookie file loaded for this session")
    elif st.session_state.get("uploaded_cookie_path"):
        st.info("Session cookie file active")
    if resolve_cookies() and not st.session_state.get("uploaded_cookie_path"):
        st.info("Using cookies from secrets")
    if not resolve_cookies():
        st.caption("No cookies set. Fresh YouTube cookies unlock 256 kbps AAC and reduce bot detection.")

    po_token, visitor_data = get_po_token()
    if po_token and visitor_data:
        st.success("PO Token and Visitor Data active (web_creator client enabled)")
    else:
        st.caption(
            "No PO_TOKEN / VISITOR_DATA secrets. "
            "Add these in Streamlit Cloud secrets to enable web_creator client fallback."
        )

    try:
        proxy_on = bool(st.secrets.get("PROXY_URL", ""))
    except Exception:
        proxy_on = False
    try:
        from yt_dlp.version import __version__ as ytv
    except Exception:
        ytv = "?"
    st.caption(
        f"Proxy: {'active' if proxy_on else 'not set'}  ·  yt-dlp {ytv}  ·  v{VERSION}  ·  "
        f"Clients: android_vr, web_safari, web, tv_downgraded, web_creator"
    )

# ---------------- QUEUE ----------------
if len(st.session_state.queue) > 1:
    st.divider()
    st.write("Queue:")
    idx = st.session_state.current
    for i, t in enumerate(st.session_state.queue):
        c0, c1 = st.columns([1, 5])
        if t["thumb"]:
            c0.image(t["thumb"], width=48)
        marker = "▶ " if i == idx else ""
        if c1.button(f"{marker}{t['title']}", key=f"qu{i}", use_container_width=True):
            st.session_state.current = i
            st.rerun()
