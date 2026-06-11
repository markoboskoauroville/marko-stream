# Stream Player

A Streamlit app that streams music from YouTube Music in the highest available audio quality. Search for artists or songs, or paste a song or playlist link, and listen with album art. Audio only, no video, no ads.

## Features

* Search YouTube Music directly from the app
* Paste a song or playlist link to load a full queue
* Album art, title and artist display
* Highest quality audio, including 256kbps AAC with YouTube Premium cookies
* Cookie support through Streamlit secrets or file upload
* Previous and Next navigation with a visual queue

## Files

* `stream_player.py` is the main app
* `requirements.txt` lists Python dependencies
* `packages.txt` installs ffmpeg on Streamlit Cloud

## Deploy on Streamlit Cloud

1. Push this repository to GitHub
2. Go to share.streamlit.io and sign in with GitHub
3. Create app, choose this repo, set main file path to `stream_player.py`
4. Deploy

## Cookies for premium quality

YouTube Premium cookies unlock the 256kbps AAC format and prevent bot detection blocks on cloud servers.

Export cookies in Netscape format from your browser using an extension such as Get cookies.txt LOCALLY while logged into YouTube Music.

Then either:

* Add them in Streamlit Cloud under Settings, Secrets:

```
YT_COOKIES = """
# Netscape HTTP Cookie File
...full content of your cookies.txt...
"""
```

* Or upload the cookies.txt file in the app sidebar for the current session

Cookies expire after some weeks. If playback starts failing with bot errors, refresh the cookie.

## Run locally

```
pip install -r requirements.txt
streamlit run stream_player.py
```
