import os
import json
import tempfile
import streamlit as st
import yt_dlp

st.set_page_config(page_title="Stream Player", page_icon="🎵", layout="centered")
st.title("Stream Player")
st.caption("YouTube Music streaming in highest available quality.")

CACHE_DIR = os.path.join(tempfile.gettempdir(), "stream_player_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

COOKIE_PATH = os.path.join(tempfile.gettempdir(), "stream_player_cookies.txt")


def resolve_cookies():
    """Uploaded cookie file wins, otherwise fall back to secrets. Returns path or None."""
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


def with_cookies(opts):
    o = dict(opts)
    c = resolve_cookies()
    if c:
        o["cookiefile"] = c
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
    # 141 = 256kbps AAC (Premium tier on YouTube Music), then best audio by bitrate
    "format": "141/774/bestaudio[ext=m4a]/bestaudio/best",
    "format_sort": ["abr", "asr"],
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
    """Return list of track dicts for a song or playlist link."""
    with yt_dlp.YoutubeDL(with_cookies(BASE_OPTS)) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist" or "entries" in info:
        return [to_track(e) for e in (info.get("entries") or []) if e]
    return [to_track(info)]


def search_music(query, limit=10):
    """Search YouTube Music songs."""
    url = f"https://music.youtube.com/search?q={query}#songs"
    opts = with_cookies(BASE_OPTS)
    opts["playlist_items"] = f"1:{limit}"
    info = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        pass
    if info and info.get("entries"):
        return [to_track(e) for e in info["entries"] if e][:limit]
    # fallback so search never comes back empty just because YTM extraction changed
    with yt_dlp.YoutubeDL(with_cookies(BASE_OPTS)) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    return [to_track(e) for e in (info.get("entries") or []) if e]


def _find_cached(video_id):
    for f in os.listdir(CACHE_DIR):
        if f.startswith(video_id + ".") and not f.endswith(".json"):
            return os.path.join(CACHE_DIR, f)
    return None


@st.cache_data(show_spinner=False, max_entries=30)
def fetch_audio(video_id):
    """Download audio in highest quality. Returns (path, meta dict)."""
    meta_path = os.path.join(CACHE_DIR, video_id + ".json")
    cached = _find_cached(video_id)
    if cached and os.path.exists(meta_path):
        with open(meta_path) as f:
            return cached, json.load(f)

    urls = [
        f"https://music.youtube.com/watch?v={video_id}",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    info, last_err = None, None
    for u in urls:
        try:
            with yt_dlp.YoutubeDL(with_cookies(DL_OPTS)) as ydl:
                info = ydl.extract_info(u, download=True)
            break
        except Exception as e:
            last_err = e
    if info is None:
        raise RuntimeError(f"All sources failed: {last_err}")

    path = _find_cached(video_id)
    if not path:
        raise RuntimeError("Download failed")
    meta = {
        "abr": info.get("abr"),
        "acodec": info.get("acodec"),
        "format_id": info.get("format_id"),
        "duration": info.get("duration"),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    return path, meta


if "queue" not in st.session_state:
    st.session_state.queue = []
if "current" not in st.session_state:
    st.session_state.current = None

with st.sidebar:
    st.subheader("Cookies")
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
        st.caption("No cookies set. Premium cookies unlock 256kbps AAC.")

tab_search, tab_link = st.tabs(["Search", "Paste link"])

with tab_search:
    q = st.text_input("Artist or song")
    if st.button("Search", type="primary") and q.strip():
        with st.spinner("Searching YouTube Music..."):
            try:
                st.session_state.search_results = search_music(q.strip())
            except Exception as e:
                st.error(f"Search failed: {e}")
    for i, tr in enumerate(st.session_state.get("search_results", [])):
        c0, c1, c2 = st.columns([1, 4, 1])
        if tr["thumb"]:
            c0.image(tr["thumb"], width=56)
        c1.write(f"**{tr['title']}**")
        if tr["artist"]:
            c1.caption(tr["artist"])
        if c2.button("Play", key=f"sr{i}"):
            st.session_state.queue = st.session_state.search_results
            st.session_state.current = i
            st.rerun()

with tab_link:
    url = st.text_input("YouTube Music link (song or playlist)")
    if st.button("Load", type="primary") and url.strip():
        with st.spinner("Reading link..."):
            try:
                st.session_state.queue = get_entries(url.strip())
                st.session_state.current = 0 if st.session_state.queue else None
                st.rerun()
            except Exception as e:
                st.error(f"Could not read this link: {e}")

st.divider()

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

    with st.spinner("Fetching audio..."):
        try:
            path, meta = fetch_audio(tr["id"])
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            mime = {"m4a": "audio/mp4", "webm": "audio/webm", "opus": "audio/ogg",
                    "mp3": "audio/mpeg", "ogg": "audio/ogg"}.get(ext, "audio/mp4")
            size_mb = os.path.getsize(path) / (1024 * 1024)
            with open(path, "rb") as f:
                st.audio(f.read(), format=mime, autoplay=True)
            abr = meta.get("abr")
            rate = f"{abr:.0f} kbps" if abr else "unknown bitrate"
            codec = meta.get("acodec") or ext
            dur = meta.get("duration")
            dur_s = f" | {int(dur // 60)}:{int(dur % 60):02d}" if dur else ""
            st.caption(f"{rate} | {codec} | {size_mb:.1f} MB{dur_s} | format {meta.get('format_id')}")
        except Exception as e:
            st.error(f"Playback failed: {e}")

    p, n = st.columns(2)
    if p.button("Previous", disabled=idx <= 0):
        st.session_state.current = idx - 1
        st.rerun()
    if n.button("Next", disabled=idx >= len(st.session_state.queue) - 1):
        st.session_state.current = idx + 1
        st.rerun()

    has_next = idx < len(st.session_state.queue) - 1
    if st.session_state.get("play_all") and has_next:
        import streamlit.components.v1 as components
        components.html(
            """
            <script>
            const doc = window.parent.document;
            function hook() {
              const audios = doc.querySelectorAll('audio');
              if (!audios.length) { setTimeout(hook, 500); return; }
              const a = audios[audios.length - 1];
              if (a.dataset.hooked) return;
              a.dataset.hooked = '1';
              a.addEventListener('ended', () => {
                const btns = doc.querySelectorAll('button');
                for (const b of btns) {
                  if (b.innerText.trim() === 'Next' && !b.disabled) { b.click(); break; }
                }
              });
            }
            hook();
            </script>
            """,
            height=0,
        )

    if len(st.session_state.queue) > 1:
        st.write("Queue:")
        for i, t in enumerate(st.session_state.queue):
            marker = "▶ " if i == idx else ""
            c0, c1, c2 = st.columns([1, 4, 1])
            if t["thumb"]:
                c0.image(t["thumb"], width=40)
            c1.write(f"{marker}{t['title']}")
            if c2.button("Play", key=f"qu{i}"):
                st.session_state.current = i
                st.rerun()
else:
    st.info("Search for an artist or paste a link to start listening.")
