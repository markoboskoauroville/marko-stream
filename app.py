import os
import json
import base64
import tempfile
import datetime
import streamlit as st
import streamlit.components.v1 as components
import yt_dlp

VERSION = 10

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
    o["nocheckcertificate"] = True   # Streamlit Cloud has SSL inspection
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

DL_OPTS_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
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


def probe_formats(video_id, use_cookies=True):
    """Return list of available formats for a video ID, or raise."""
    opts = with_cookies({
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }, use_cookies=use_cookies)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=False
        )
    return info.get("formats") or []


def pick_best_audio_format(formats):
    """
    Pick the best audio-only format by bitrate.
    Falls back to any format with audio if no audio-only exists.
    Returns format id string or None.
    """
    audio_only = [f for f in formats if not f.get("vcodec") or f["vcodec"] == "none"]
    pool = audio_only if audio_only else formats
    if not pool:
        return None
    # sort by abr descending, then filesize descending
    pool_sorted = sorted(
        pool,
        key=lambda f: (f.get("abr") or 0, f.get("filesize") or 0),
        reverse=True,
    )
    return pool_sorted[0]["format_id"]


@st.cache_data(show_spinner=False, max_entries=20)
def fetch_audio(video_id):
    """
    Two-phase download:
    1. Probe available formats explicitly (no format selector guessing).
    2. Download with the exact format_id we found.
    Falls back to format selector strings if probing fails.
    """
    meta_path = os.path.join(CACHE_DIR, video_id + ".json")
    cached = _find_cached(video_id)
    if cached and os.path.exists(meta_path):
        with open(meta_path) as f:
            return cached, json.load(f)

    wu = f"https://www.youtube.com/watch?v={video_id}"
    po_token, visitor_data = get_po_token()
    errors = []

    # Phase 1: probe formats
    fmt_id = None
    probe_clients = ["android_vr", "web_safari", "tv_downgraded", "web_creator", "web"]
    for client in probe_clients:
        for use_ck in (True, False):
            extra = {"extractor_args": {"youtube": {"player_client": [client]}}}
            if po_token and visitor_data and client == "web_creator":
                extra["extractor_args"]["youtube"]["po_token"] = [f"web+{po_token}"]
                extra["extractor_args"]["youtube"]["visitor_data"] = [visitor_data]
            opts = with_cookies({
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                **extra,
            }, use_cookies=use_ck)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(wu, download=False)
                fmts = info.get("formats") or []
                fmt_id = pick_best_audio_format(fmts)
                if fmt_id:
                    break
                errors.append(f"probe {client} ({'ck' if use_ck else 'no-ck'}): 0 formats returned")
            except Exception as e:
                errors.append(f"probe {client} ({'ck' if use_ck else 'no-ck'}): {str(e)[:100]}")
        if fmt_id:
            break

    # Phase 2: download
    dl_clients = ["android_vr", "web_safari", "tv_downgraded", "web_creator", "web"]
    # build format string: explicit id first, then permissive fallbacks
    fmt_str = f"{fmt_id}/bestaudio*/best" if fmt_id else "bestaudio*/best"

    info, last_err = None, None
    for client in dl_clients:
        for use_ck in (True, False):
            extra = {"extractor_args": {"youtube": {"player_client": [client]}}}
            try:
                opts = with_cookies({**DL_OPTS_BASE, "format": fmt_str, **extra}, use_cookies=use_ck)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(wu, download=True)
                break
            except Exception as e:
                last_err = e
                errors.append(f"dl {client} ({'ck' if use_ck else 'no-ck'}): {str(e)[:100]}")
        if info:
            break

    if info is None:
        detail = "\n".join(errors)
        raise RuntimeError(
            f"All attempts failed. Probed fmt_id={fmt_id!r}\n\n{detail}\n\nLast: {last_err}"
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
      for (let j = 0; j < step; j += 16) { const v = Math.abs(data[i*step+j]||0); if(v>max)max=v; }
      peaks.push(max);
    }
  }).catch(()=>{});
}
function drawWave() {
  wctx.clearRect(0,0,waveC.width,waveC.height);
  if (!cbW.checked||!peaks) return;
  const h=waveC.height, mid=h/2, prog=au.duration?au.currentTime/au.duration:0;
  for(let i=0;i<peaks.length;i++){
    const ph=Math.max(2,peaks[i]*(h-8));
    wctx.fillStyle=(i/peaks.length)<=prog?'#e05a00':'#444';
    wctx.fillRect(i,mid-ph/2,1,ph);
  }
}
function drawOsc() {
  octx.clearRect(0,0,oscC.width,oscC.height);
  if(!cbO.checked||!analyser)return;
  const arr=new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(arr);
  octx.strokeStyle='#e05a00'; octx.lineWidth=2; octx.beginPath();
  const sw=oscC.width/arr.length;
  for(let i=0;i<arr.length;i++){const y=(arr[i]/255)*oscC.height;if(i===0)octx.moveTo(0,y);else octx.lineTo(i*sw,y);}
  octx.stroke();
}
function loop(){drawWave();drawOsc();requestAnimationFrame(loop);}
loop();
au.addEventListener('play',()=>{setup();if(actx)actx.resume();});
waveC.addEventListener('click',(e)=>{
  if(!au.duration)return;
  const r=waveC.getBoundingClientRect();
  au.currentTime=((e.clientX-r.left)/r.width)*au.duration;
});
const AUTONEXT=%AUTONEXT%;
au.addEventListener('ended',()=>{
  if(!AUTONEXT)return;
  const btns=window.parent.document.querySelectorAll('button');
  for(const b of btns){if(b.innerText.trim()==='Next'&&!b.disabled){b.click();break;}}
});
</script>
"""
    html = (html.replace("%MIME%", mime)
                .replace("%B64%", b64)
                .replace("%AUTONEXT%", "true" if autonext else "false"))
    components.html(html, height=300)


# ── cookie inspector ──────────────────────────────────────────────────────────

def parse_cookies_file(path):
    """Parse Netscape cookies.txt and return list of dicts."""
    cookies = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, flag, path_, secure, expires, name, value = parts[:7]
                try:
                    exp_ts = int(expires)
                    exp_dt = datetime.datetime.utcfromtimestamp(exp_ts) if exp_ts > 0 else None
                except Exception:
                    exp_dt = None
                cookies.append({
                    "domain": domain,
                    "path": path_,
                    "secure": flag.upper() == "TRUE",
                    "expires": exp_dt,
                    "name": name,
                    "value_len": len(value),
                })
    except Exception as e:
        return [], str(e)
    return cookies, None


def show_cookie_info():
    path = resolve_cookies()
    if not path or not os.path.exists(path):
        st.warning("No cookie file found. Upload one below or add YT_COOKIES to secrets.")
        return

    cookies, err = parse_cookies_file(path)
    if err:
        st.error(f"Could not parse cookies file: {err}")
        return

    now = datetime.datetime.utcnow()
    yt_cookies = [c for c in cookies if "youtube" in c["domain"] or "google" in c["domain"]]

    st.write(f"Total cookies in file: {len(cookies)}, YouTube/Google cookies: {len(yt_cookies)}")

    expired_count = sum(1 for c in yt_cookies if c["expires"] and c["expires"] < now)
    valid_count = sum(1 for c in yt_cookies if c["expires"] and c["expires"] >= now)
    no_expiry_count = sum(1 for c in yt_cookies if not c["expires"])

    col1, col2, col3 = st.columns(3)
    col1.metric("Valid", valid_count, delta=None)
    col2.metric("Expired", expired_count, delta=None)
    col3.metric("No expiry", no_expiry_count, delta=None)

    # Key auth cookies
    key_names = {"SAPISID", "HSID", "SSID", "SID", "APISID", "__Secure-3PSID",
                 "__Secure-3PAPISID", "LOGIN_INFO", "VISITOR_INFO1_LIVE", "YSC"}
    found_keys = {c["name"] for c in yt_cookies if c["name"] in key_names}
    missing_keys = key_names - found_keys

    if found_keys:
        st.success(f"Auth cookies present: {', '.join(sorted(found_keys))}")
    if missing_keys:
        st.warning(f"Auth cookies missing: {', '.join(sorted(missing_keys))}")

    # Expiry timeline
    with st.expander("All YouTube/Google cookies"):
        rows = []
        for c in sorted(yt_cookies, key=lambda x: x["domain"] + x["name"]):
            if c["expires"]:
                days_left = (c["expires"] - now).days
                exp_str = c["expires"].strftime("%Y-%m-%d") + f"  ({days_left}d)"
                status = "✅" if days_left > 0 else "❌ EXPIRED"
            else:
                exp_str = "session / no expiry"
                status = "✅"
            rows.append({
                "status": status,
                "name": c["name"],
                "domain": c["domain"],
                "expires": exp_str,
                "secure": "🔒" if c["secure"] else "",
                "value_len": c["value_len"],
            })
        st.table(rows)

    # Live test
    st.write("Live cookie test — tries to resolve a known public video with your cookies:")
    if st.button("Test cookies now"):
        TEST_ID = "jNQXAC9IVRw"  # "Me at the zoo" — first ever YouTube video, always public
        with st.spinner("Testing…"):
            try:
                opts = with_cookies({
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "noplaylist": True,
                    "nocheckcertificate": True,
                }, use_cookies=True)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(
                        f"https://www.youtube.com/watch?v={TEST_ID}", download=False
                    )
                fmts = info.get("formats") or []
                audio_fmts = [f for f in fmts if not f.get("vcodec") or f["vcodec"] == "none"]
                st.success(
                    f"Cookies work. Title: '{info.get('title')}'. "
                    f"Total formats: {len(fmts)}, audio-only: {len(audio_fmts)}."
                )
                if audio_fmts:
                    best = sorted(audio_fmts, key=lambda f: f.get("abr") or 0, reverse=True)[0]
                    st.info(
                        f"Best audio format: {best.get('format_id')} "
                        f"{best.get('ext')} {best.get('abr') or '?'} kbps "
                        f"{best.get('acodec') or ''}"
                    )
            except Exception as e:
                err = str(e)
                if "Sign in" in err or "bot" in err.lower():
                    st.error("Cookies rejected by YouTube (bot detection or session expired). Re-export fresh cookies from your browser.")
                elif "format" in err.lower():
                    st.error(f"Connected but no formats available: {err}")
                else:
                    st.error(f"Test failed: {err}")

    st.write("")
    st.caption(
        "How to export fresh cookies: in Chrome/Firefox, install the extension "
        "'Get cookies.txt LOCALLY', go to youtube.com while logged in, click the extension "
        "and export for youtube.com. Upload the file here or paste into the YT_COOKIES secret."
    )


# ── session state ─────────────────────────────────────────────────────────────

if "queue" not in st.session_state:
    st.session_state.queue = []
if "current" not in st.session_state:
    st.session_state.current = None

st.caption(f"v{VERSION}")

# ── player ────────────────────────────────────────────────────────────────────

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
            st.error("Playback failed")
            with st.expander("Show error detail"):
                st.code(str(e))
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={tr['id']}&format=json",
                    headers={"User-Agent": CHROME_UA})
                urllib.request.urlopen(req, timeout=8)
                st.warning(
                    "Probe: video EXISTS on YouTube from this server region. "
                    "Problem is likely expired cookies or bot detection. "
                    "Go to Setup and run the cookie test."
                )
            except Exception:
                st.warning(
                    "Probe: video NOT reachable from server region (US). "
                    "It may be deleted, private, or geo-locked. "
                    "Add PROXY_URL secret for geo-lock bypass."
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

# ── tabs ──────────────────────────────────────────────────────────────────────

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
    show_cookie_info()

    st.divider()
    up = st.file_uploader("Upload cookies.txt (Netscape format)", type=["txt"])
    if up is not None:
        path = os.path.join(tempfile.gettempdir(), "uploaded_cookies.txt")
        with open(path, "wb") as f:
            f.write(up.getvalue())
        st.session_state.uploaded_cookie_path = path
        st.success("Cookie file loaded for this session")
        st.rerun()
    elif st.session_state.get("uploaded_cookie_path"):
        st.info("Session cookie file active")
        if st.button("Clear session cookies"):
            st.session_state.uploaded_cookie_path = None
            st.rerun()

    try:
        proxy_on = bool(st.secrets.get("PROXY_URL", ""))
    except Exception:
        proxy_on = False
    po_token, visitor_data = get_po_token()
    try:
        from yt_dlp.version import __version__ as ytv
    except Exception:
        ytv = "?"
    st.caption(
        f"Proxy: {'active' if proxy_on else 'not set'}  ·  "
        f"PO Token: {'active' if po_token else 'not set'}  ·  "
        f"yt-dlp {ytv}  ·  v{VERSION}"
    )

# ── queue ─────────────────────────────────────────────────────────────────────

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
