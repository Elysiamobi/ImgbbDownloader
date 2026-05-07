import re
import requests
from lxml import html as lxml_html
import time
import random

import task_status

def album_need_password(html: str) -> bool:
    return 'name="content-password"' in html

def unlock_album(session: requests.Session, album_url: str, password: str):
    r = session.get(album_url, timeout=10)
    r.raise_for_status()

    token = re.search(r'name="auth_token" value="([^"]+)"', r.text)
    if not token:
        return False, "未能获取到 auth_token"

    auth_token = token.group(1)

    res = session.post(
        album_url,
        data={
            "content-password": password,
            "auth_token": auth_token
        },
        headers={
            "Referer": album_url,
            "Origin": "https://ibb.co"
        },
        timeout=10,
        allow_redirects=True
    )
    res.raise_for_status()

    verify = session.get(album_url, timeout=10)
    verify.raise_for_status()

    if album_need_password(verify.text):
        return False, "密码错误或解锁失败"

    return True, "解锁成功"

def extract_image_pages(
        session,
        album_url,
        log_func=print
):
    image_pages = []

    if "?" not in album_url:
        current_url = f"{album_url}?sort=name_asc&page=1"
    else:
        current_url = album_url

    while True:
        try:
            r = session.get(current_url, timeout=10)
            r.raise_for_status()

            found_on_page = re.findall(r"https://ibb\.co/[a-zA-Z0-9]{7,8}", r.text)

            new_links_on_page = list(dict.fromkeys(found_on_page))

            count_before = len(image_pages)
            for link in new_links_on_page:
                if link not in image_pages:
                    image_pages.append(link)

            added_count = len(image_pages) - count_before

            tree = lxml_html.fromstring(r.text)
            next_href = tree.xpath('//li[contains(@class, "pagination-next")]/a/@href')

            if next_href and next_href[0] != "#" and next_href[0] != current_url:
                current_url = next_href[0]
                time.sleep(random.uniform(0.5, 1.0))
            else:
                break

        except Exception as e:
            log_func(f"❗ 分页解析中断：{e}")
            break

    return image_pages

def extract_original_image_url(
        session: requests.Session,
        image_page_url: str,
        log_func=print
) -> str:

    r = session.get(image_page_url, timeout=10)
    r.raise_for_status()

    m = re.search(
        r'<meta property="og:image" content="([^"]+)"',
        r.text
    )

    if not m:
        log_func("❌ 未在页面中找到 og:image 元标签")
        raise RuntimeError("未找到原图链接")

    img_url = m.group(1)
    log_func(f"🖼️ 获取原图链接：{img_url}")

    return img_url

def extract_subfolder(link: str) -> str:
    parsed = link.rstrip("/").split("/")
    if "album" in parsed:
        idx = parsed.index("album")
        if idx + 1 < len(parsed):
            return "album_" + parsed[idx + 1]
    return parsed[-1] if parsed else ""

def extract_album_name(html: str) -> str:
    """从HTML中提取相册名称"""
    # 尝试从<title>标签提取
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()
        # 移除常见后缀，如 " - Imgbb" 或 " | Imgbb"
        title = re.split(r'\s*[-|]\s*(?:imgbb|ibb\.co)\s*$', title, flags=re.IGNORECASE)[0].strip()
        if title and title.lower() not in ('imgbb', 'ibb.co', 'image hosting', 'upload image'):
            # 清理文件名中不允许的字符
            title = re.sub(r'[<>:"/\\|?*]', '_', title)
            return title

    # 尝试从<h1>或<h2>标签提取
    h1_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
    if h1_match:
        name = h1_match.group(1).strip()
        if name:
            return re.sub(r'[<>:"/\\|?*]', '_', name)

    h2_match = re.search(r'<h2[^>]*>([^<]+)</h2>', html, re.IGNORECASE)
    if h2_match:
        name = h2_match.group(1).strip()
        if name:
            return re.sub(r'[<>:"/\\|?*]', '_', name)

    return ""

def process_download_links_until_success(
        links,
        log_func=print,
        album_password=None
):
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://ibb.co",
        "Origin": "https://ibb.co"
    })

    for link in links:
        try:
            if "/album/" not in link:
                img_url = extract_original_image_url(
                    session,
                    link,
                    log_func
                )
                task_status.add_link(img_url, subfolder=extract_subfolder(link))
                continue

            log_func(f"📁 解析相册：{link}")

            r = session.get(link, timeout=10)
            r.raise_for_status()

            if album_need_password(r.text):

                if not album_password:
                    raise RuntimeError("该相册需要密码")

                ok, msg = unlock_album(
                    session,
                    link,
                    album_password
                )
                if not ok:
                    raise RuntimeError(msg)

                log_func("🔓 相册解锁成功")

            # 获取相册页面内容并检查是否解锁
            album_page = session.get(link, timeout=10)
            album_page.raise_for_status()

            if album_need_password(album_page.text):
                raise RuntimeError("相册仍处于锁定状态")

            pages = extract_image_pages(
                session,
                link,
                log_func
            )
            if not pages:
                raise RuntimeError("相册中未解析到任何图片")

            log_func(f"📁 相册共 {len(pages)} 张图片")

            # 提取相册ID
            album_id = ""
            parsed = link.rstrip("/").split("/")
            if "album" in parsed:
                idx = parsed.index("album")
                if idx + 1 < len(parsed):
                    album_id = parsed[idx + 1]

            # 尝试提取相册名称作为文件夹名
            album_name = extract_album_name(album_page.text)
            if album_name and album_id:
                album_subfolder = f"{album_name}_{album_id}"
                log_func(f"📁 相册名称: {album_name} (ID: {album_id})")
            elif album_name:
                album_subfolder = album_name
                log_func(f"📁 相册名称: {album_name}")
            else:
                album_subfolder = extract_subfolder(link)
            for idx, page_url in enumerate(pages, start=1):

                img_url = extract_original_image_url(
                    session,
                    page_url,
                    log_func
                )
                task_status.add_link(img_url, subfolder=album_subfolder)

        except Exception as e:
            log_func(f"❗ 解析失败 {link} → {e}")
            return False
