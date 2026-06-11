import re

with open("app.py", "r") as f:
    src = f.read()

old = '''COOKIE_PATH = os.path.join(tempfile.gettempdir(), "stream_player_cookies.txt")


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
    return None'''

new = '''COOKIE_PATH = os.path.join(tempfile.gettempdir(), "stream_player_cookies.txt")
LOCAL_COOKIE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


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
    if os.path.isfile(LOCAL_COOKIE_PATH):
        return LOCAL_COOKIE_PATH
    return None'''

if old in src:
    src = src.replace(old, new)
    with open("app.py", "w") as f:
        f.write(src)
    print("PATCHED OK")
else:
    print("NOT FOUND")
