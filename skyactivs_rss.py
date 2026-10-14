import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://www.skyactivs.com/news"
OUTPUT_FILE = "skyactivs.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_url(url):
    parsed = urlparse(url)

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{parsed.path}"
    )


def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


def fetch_page(url):
    for attempt in range(1, 4):
        try:
            print(f"取得 {attempt}/3: {url}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            print(f"HTTP: {response.status_code}")

            response.raise_for_status()

            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

            return response.text

        except requests.RequestException as e:
            print(f"取得失敗: {e}")

            if attempt < 3:
                print("10秒待って再試行します。")
                time.sleep(10)

    return None


def parse_date(text):
    if not text:
        return None

    match = re.search(
        r"(20\d{2})\."
        r"(\d{1,2})\."
        r"(\d{1,2})",
        text,
    )

    if not match:
        return None

    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            12,
            0,
            0,
            tzinfo=JST,
        )

    except ValueError:
        return None


def is_news_url(url):
    parsed = urlparse(url)

    if parsed.netloc not in (
        "www.skyactivs.com",
        "skyactivs.com",
    ):
        return False

    return bool(
        re.fullmatch(
            r"/news/\d+/?",
            parsed.path,
        )
    )


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall(
            "./channel/item"
        ):
            link = (
                item.findtext(
                    "link",
                    "",
                )
                .strip()
            )

            guid = (
                item.findtext(
                    "guid",
                    "",
                )
                .strip()
            )

            if not guid and link:
                guid = make_guid(link)

            if not guid:
                continue

            items[guid] = {
                "title": item.findtext(
                    "title",
                    "",
                ),
                "link": link,
                "description": item.findtext(
                    "description",
                    "",
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    "",
                ),
                "guid": guid,
                "category": item.findtext(
                    "category",
                    "",
                ),
            }

    except Exception as e:
        print(
            f"既存RSS読込失敗: {e}"
        )

    return items


def find_article_data(a):
    current = a

    for _ in range(8):
        if current is None:
            break

        text = clean_text(
            current.get_text(
                " ",
                strip=True,
            )
        )

        published = parse_date(text)

        if published:
            category = ""

            category_match = re.search(
                r"\b("
                r"ACTIVITY|"
                r"TEAM|"
                r"GAME|"
                r"FAN\s*CLUB|"
                r"OTHERS"
                r")\b",
                text,
                re.IGNORECASE,
            )

            if category_match:
                category = (
                    category_match
                    .group(1)
                    .upper()
                    .replace("  ", " ")
                )

            return (
                published,
                category,
                text,
            )

        current = current.parent

    return None, "", ""


def clean_title(
    raw_text,
    category,
):
    text = clean_text(raw_text)

    # 日付を除去
    text = re.sub(
        r"20\d{2}\."
        r"\d{1,2}\."
        r"\d{1,2}"
        r"\[[A-Z]{3}\]",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )

    text = clean_text(text)

    # カテゴリを先頭から除去
    if category:
        text = re.sub(
            r"^"
            + re.escape(category)
            + r"\s*",
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        )

    # 一覧の矢印文字を除去
    text = re.sub(
        r"\s*arrow_forward\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return clean_text(text)


def extract_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    articles = {}

    for a in soup.find_all(
        "a",
        href=True,
    ):
        link = canonicalize_url(
            urljoin(
                SOURCE_URL,
                a["href"],
            )
        )

        if not is_news_url(link):
            continue

        published, category, container_text = (
            find_article_data(a)
        )

        if published is None:
            continue

        # まずリンクそのもののテキストを使用
        raw_title = clean_text(
            a.get_text(
                " ",
                strip=True,
            )
        )

        title = clean_title(
            raw_title,
            category,
        )

        # リンク文字列が画像等だけの場合は
        # 親ブロックのテキストを利用
        if (
            not title
            or len(title) < 4
        ):
            title = clean_title(
                container_text,
                category,
            )

        if not title:
            continue

        if title in (
            "NEWS",
            "ニュース",
            "arrow_forward",
        ):
            continue

        candidate = {
            "title": title,
            "link": link,
            "date": published,
            "category": category,
        }

        existing = articles.get(link)

        if existing is None:
            articles[link] = candidate

        else:
            # PICK UP NEWSとNEWS LISTの両方に
            # 同じ記事が出るためURLで重複排除。
            # より自然なタイトルを残す。
            if (
                len(title)
                > len(existing["title"])
            ):
                articles[link] = candidate

    result = list(
        articles.values()
    )

    result.sort(
        key=lambda x: (
            x["date"],
            x["link"],
        ),
        reverse=True,
    )

    return result


def build_rss(
    existing_items,
    current_articles,
):
    merged = dict(existing_items)

    new_count = 0

    for article in current_articles:
        guid = make_guid(
            article["link"]
        )

        if guid not in merged:
            new_count += 1

        description = (
            "マツダスカイアクティブズ広島"
        )

        if article["category"]:
            description = (
                f"カテゴリ: "
                f"{article['category']} / "
                f"{description}"
            )

        merged[guid] = {
            "title": article["title"],
            "link": article["link"],
            "description": description,
            "pubDate": format_datetime(
                article["date"]
            ),
            "guid": guid,
            "category": article[
                "category"
            ],
        }

    def get_pubdate(item):
        try:
            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt

        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=get_pubdate,
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0",
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = (
        "マツダスカイアクティブズ広島 NEWS"
    )

    ET.SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "マツダスカイアクティブズ広島"
        "公式サイトのNEWS"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        datetime.now(JST)
    )

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = data["title"]

        ET.SubElement(
            item,
            "link",
        ).text = data["link"]

        ET.SubElement(
            item,
            "description",
        ).text = data[
            "description"
        ]

        ET.SubElement(
            item,
            "pubDate",
        ).text = data[
            "pubDate"
        ]

        if data.get("category"):
            ET.SubElement(
                item,
                "category",
            ).text = data[
                "category"
            ]

        guid_element = ET.SubElement(
            item,
            "guid",
        )

        guid_element.set(
            "isPermaLink",
            "false",
        )

        guid_element.text = data[
            "guid"
        ]

    tree = ET.ElementTree(rss)

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return (
        len(sorted_items),
        new_count,
    )


def main():
    existing_items = (
        load_existing_items()
    )

    print(
        f"既存RSS件数: "
        f"{len(existing_items)}"
    )

    html = fetch_page(
        SOURCE_URL
    )

    if html is None:
        if existing_items:
            print(
                "取得に失敗したため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "SKYACTIVS NEWSの"
            "取得に失敗しました。"
        )

    print(
        f"HTML文字数: {len(html)}"
    )

    articles = extract_articles(
        html
    )

    print()
    print(
        f"記事取得件数: "
        f"{len(articles)}"
    )

    print()
    print(
        "=== 取得記事 ==="
    )

    for i, article in enumerate(
        articles,
        1,
    ):
        category = (
            f"[{article['category']}] "
            if article["category"]
            else ""
        )

        print()
        print(
            f"{i:3d}. "
            f"{article['date'].strftime('%Y-%m-%d')} "
            f"{category}"
            f"{article['title']}"
        )

        print(
            f"     {article['link']}"
        )

    # 一覧ページなのに極端に少ない場合は
    # サイト構造変更の可能性があるため
    # 既存XMLを上書きしない
    if len(articles) < 10:
        if existing_items:
            print()
            print(
                "記事取得件数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            f"記事取得件数が少なすぎます: "
            f"{len(articles)}"
        )

    total_count, new_count = (
        build_rss(
            existing_items,
            articles,
        )
    )

    print()
    print(
        f"今回の新規記事: "
        f"{new_count}"
    )

    print(
        f"RSS保存件数: "
        f"{total_count}"
    )

    print(
        f"保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()