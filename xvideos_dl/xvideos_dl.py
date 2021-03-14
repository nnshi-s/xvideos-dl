from typing import Any, Dict, List

import html
import re
import time
from collections import namedtuple
from pathlib import Path

from requests import Session
from requests.cookies import cookiejar_from_dict
from rich.console import Console

from . import constant as c

console = Console()
session = Session()
Video = namedtuple("Video", "vid vname pname")


def parse_cookies(cookie: str) -> Dict[str, str]:
    try:
        key_values = cookie.split("; ")
        return {kv.split("=")[0]: kv.split("=")[1] for kv in key_values}
    except IndexError:
        return {}


def save_cookie(cookie: str) -> None:
    cache_dir = Path.home() / f".{c.APP_NAME}"
    cache_dir.mkdir(exist_ok=True)
    with open(cache_dir / "cookie", "w") as f:
        f.write(cookie)


def read_cookie() -> str:
    cookie = "foo=bar"
    cache_file = Path.home() / f".{c.APP_NAME}/cookie"
    if cache_file.is_file():
        with open(cache_file) as f:
            cookie = f.read()
    return cookie


def find_from_string(pattern: str, string: str) -> str:
    find = re.search(pattern, string)
    if not find:
        raise ValueError(f"can't download video from URL: {string}")
    return find.group()


def parse_video_id(index: str) -> str:
    return find_from_string(r"(?<=video)\d+(?=/)", index)


def parse_video_name(index: str) -> str:
    return find_from_string(r"(?<=\d/).+(?=[/])*", index)


def parse_playlist_id(index: str) -> str:
    return find_from_string(r"(?<=/favorite/)\d+(?=/)", index)


def safe_filename(filename: str) -> str:
    return "".join([char for char in filename if char not in r'\/:*?"<>|']).strip()


def get_video_full_name(index: str) -> str:
    resp = session.get(index, timeout=c.TIMEOUT)
    resp.raise_for_status()
    title_tab = re.search(r'(?<=<meta property="og:title" content=").*?(?="\s*/>)', resp.text)
    if title_tab:
        return safe_filename(html.unescape(title_tab.group()))
    return ""


def request_with_cookie(method: str, url: str, return_when: str) -> Dict[str, Any]:
    cookie_raw = read_cookie()
    cookies = parse_cookies(cookie_raw)

    while 1:
        session.cookies = cookiejar_from_dict(cookies)
        resp = session.request(method, url, timeout=c.TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        keys = return_when.split(".")
        value = data
        for key in keys:
            value = value.get(key, {})
        if value:
            save_cookie(cookie_raw)
            break
        error = data.get("ERROR")
        if error:
            raise ValueError(f"{error} {url}")
        cookie_raw = input("The cookie has expired, please enter a new one:\n").strip()
        cookies = parse_cookies(cookie_raw)

    return data


def get_video_url(vid: str, low: bool = False) -> str:
    video_api = c.VIDEO_API.format(vid=vid)
    data = request_with_cookie("GET", video_api, return_when="URL")

    url_field = "URL"
    if low:
        url_field = "URL_LOW"

    return data.get(url_field)


def get_videos_from_play_page(page_url: str) -> Video:
    vid = parse_video_id(page_url)
    vname = get_video_full_name(page_url) or parse_video_name(page_url)
    return Video(vid=vid, vname=vname, pname="")


def get_videos_by_playlist_id(pid: str) -> List[Video]:
    playlist_api = c.PLAYLIST_API.format(pid=pid)
    data = request_with_cookie("POST", playlist_api, return_when="logged")
    playlist_name = data.get("list", {}).get("name")
    videos_info = data.get("list", {}).get("videos")
    videos = []
    for v in videos_info:
        videos.append(Video(vid=v.get("id"), vname=v.get("tf"), pname=playlist_name))

    return videos


def download(video: Video, dest: str, low: bool, overwrite: bool) -> None:
    url = get_video_url(video.vid, low)

    save_dir = Path(dest) / video.pname
    save_dir.mkdir(exist_ok=True)
    save_name = save_dir / f"{video.vname}(#{video.vid}).mp4"
    head = session.head(url, stream=True)
    size = int(head.headers["Content-Length"].strip())

    console.print(f"Video ID   : [cyan]{video.vid}[/]")
    console.print(f"Video Name : [yellow]{video.vname}[/]")
    console.print(f"Video Link : [underline]{url}[/]")
    console.print(f"Video Size : [white]{size / 1024 ** 2:.2f}[/] MB")
    console.print(f"Destination: [white]{save_name.absolute()}[/]")

    done = 0
    if save_name.is_file():
        if overwrite:
            save_name.unlink(missing_ok=True)
        else:
            done = save_name.stat().st_size

    show_process_bar = False
    if done < size:
        show_process_bar = True
        print()

    while done < size:
        time_start = time.time()
        start = done
        done += c.FRAGMENT_SIZE
        if done > size:
            done = size
        end = done
        headers = {"Range": f"bytes={start}-{end - 1}"}
        resp = session.get(url, stream=True, headers=headers, timeout=c.TIMEOUT)

        with open(save_name, "ab") as f:
            write = start
            for chunk in resp.iter_content(c.CHUNK_SIZE):
                f.write(chunk)

                write += c.CHUNK_SIZE
                percent_done = int(min(write, size) / size * 1000) / 10
                bar_done = int(percent_done * 0.6)
                console.print(f"|{'█' * bar_done}{' ' * (60 - bar_done)}| [green]{percent_done:5.1f}%[/]", end="\r")
        # Download speed
        speed = (end - start) / (time.time() - time_start)
        if speed < 1024:  # 1KB
            speed_text = f"{speed:7.2f}B/s"
        elif speed < 1048576:  # 1MB
            speed_text = f"{speed / 1024:7.2f}KB/s"
        else:
            speed_text = f"{speed / 1048576:7.2f}MB/s"
        console.print(
            f"|{'█' * bar_done}{' ' * (60 - bar_done)}| [green]{percent_done:5.1f}% {speed_text}[/]", end="\r"
        )

    if show_process_bar:
        print(end="\n\n")
