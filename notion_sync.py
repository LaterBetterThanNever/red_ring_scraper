"""
小红圈文章 → Notion 数据库同步脚本
将爬取的 Markdown 文章（含 GitHub 永久图片 URL）完整上传到 Notion 数据库

使用方法:
  python3 notion_sync.py <markdown_file> [--github-map <json_file>]
  python3 notion_sync.py <directory>
"""

import os
import re
import sys
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime

# ============ 配置 ============
# 从 config.json 读取敏感配置，不硬编码 token
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_config = {}
if os.path.exists(_config_path):
    with open(_config_path, "r", encoding="utf-8") as _f:
        _config = json.load(_f)

NOTION_TOKEN = os.environ.get(
    "NOTION_TOKEN",
    _config.get("notion_token", "")
)
# Notion 数据库 ID
NOTION_DATABASE_ID = os.environ.get(
    "NOTION_DATABASE_ID",
    _config.get("notion_database_id", "")
)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ============ Notion API ============

def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def notion_get(endpoint: str, params=None):
    r = requests.get(f"{NOTION_API}/{endpoint}", headers=notion_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def notion_post(endpoint: str, data: dict):
    r = requests.post(f"{NOTION_API}/{endpoint}", headers=notion_headers(), json=data, timeout=60)
    if r.status_code >= 400:
        logger.error(f"Notion API 错误: {r.status_code} {r.text[:500]}")
    r.raise_for_status()
    return r.json()


def verify_database():
    """验证 Notion 数据库是否可访问"""
    try:
        db = notion_get(f"databases/{NOTION_DATABASE_ID}")
        title_parts = db.get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts)
        props = db.get("properties", {})
        prop_info = {name: p["type"] for name, p in props.items()}
        logger.info(f"数据库: {title or '(未命名)'} (ID: {NOTION_DATABASE_ID})")
        logger.info(f"属性: {prop_info}")
        return True
    except Exception as e:
        logger.error(f"无法访问数据库: {e}")
        return False


# ============ Markdown → Notion Blocks ============

def md_to_notion_blocks(md_text: str, github_url_map: dict = None) -> list:
    """
    将 Markdown 转换为 Notion Block 列表。
    github_url_map: {本地相对路径: GitHub raw URL} 映射，用于替换图片路径
    """
    blocks = []
    lines = md_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 分割线
        if stripped in ("---", "***", "___") or re.match(r'^—{3,}$', stripped):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        # 图片
        img_match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)', stripped)
        if img_match:
            alt_text = img_match.group(1)
            img_path = img_match.group(2)
            # 替换为 GitHub URL
            if github_url_map and img_path in github_url_map:
                img_url = github_url_map[img_path]
            elif img_path.startswith("http"):
                img_url = img_path
            else:
                # 尝试匹配 images/filename 格式
                img_url = img_path
            blocks.append(make_image_block(img_url, alt_text))
            i += 1
            continue

        # 标题
        heading_match = re.match(r'^(#{1,6})\s+(.+)', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            block_type = f"heading_{min(level, 3)}"
            blocks.append({
                "object": "block",
                "type": block_type,
                block_type: {"rich_text": parse_rich_text(text)}
            })
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r'^>\s*', '', lines[i].strip()))
                i += 1
            blocks.append({
                "object": "block", "type": "quote",
                "quote": {"rich_text": parse_rich_text(" ".join(quote_lines))}
            })
            continue

        # 无序列表
        ul_match = re.match(r'^[-*]\s+(.+)', stripped)
        if ul_match:
            blocks.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": parse_rich_text(ul_match.group(1))}
            })
            i += 1
            continue

        # 有序列表
        ol_match = re.match(r'^\d+\.\s+(.+)', stripped)
        if ol_match:
            blocks.append({
                "object": "block", "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": parse_rich_text(ol_match.group(1))}
            })
            i += 1
            continue

        # 普通段落
        para_lines = []
        while i < len(lines):
            l = lines[i].strip()
            if (not l or l.startswith("#") or l.startswith(">") or l.startswith("- ")
                    or l.startswith("* ") or l.startswith("!")
                    or l in ("---", "***", "___")
                    or re.match(r'^—{3,}$', l)
                    or re.match(r'^\d+\.\s+', l)):
                break
            para_lines.append(l)
            i += 1
        if para_lines:
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": parse_rich_text("\n".join(para_lines))}
            })
        continue

    return blocks


def parse_rich_text(text: str) -> list:
    """解析行内格式，返回 Notion rich_text 数组"""
    result = []
    pattern = re.compile(
        r'(\*\*(.+?)\*\*)'
        r'|(\*(.+?)\*)'
        r'|(`([^`]+)`)'
        r'|(\[([^\]]+)\]\(([^)]+)\))'
    )
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() > last_end:
            plain = text[last_end:m.start()]
            if plain:
                result.append(make_text_obj(plain))
        if m.group(1):
            result.append(make_text_obj(m.group(2), bold=True))
        elif m.group(3):
            result.append(make_text_obj(m.group(4), italic=True))
        elif m.group(5):
            result.append({"type": "text", "text": {"content": m.group(6)}, "annotations": {"code": True}})
        elif m.group(7):
            result.append({"type": "text", "text": {"content": m.group(8), "link": {"url": m.group(9)}}})
        last_end = m.end()
    if last_end < len(text):
        remaining = text[last_end:]
        if remaining:
            result.append(make_text_obj(remaining))
    if not result:
        result.append(make_text_obj(text))
    return result[:100]


def make_text_obj(content, bold=False, italic=False, code=False, strikethrough=False, underline=False):
    return {
        "type": "text",
        "text": {"content": content[:2000]},
        "annotations": {"bold": bold, "italic": italic, "code": code, "strikethrough": strikethrough, "underline": underline}
    }


def make_image_block(url, alt_text=""):
    return {"object": "block", "type": "image", "image": {"type": "external", "external": {"url": url}}}


# ============ 解析 Markdown 文件 ============

def parse_md_file(filepath: str) -> dict:
    """解析 Markdown 文件，返回元信息和内容"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    title, date_str, source, content_id, image_urls = "", "", "", "", []
    body = text

    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        body = text[fm_match.end():]
        in_image_urls = False
        for line in fm.split("\n"):
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"')
                in_image_urls = False
            elif line.startswith("date:"):
                date_str = line.split(":", 1)[1].strip()
                in_image_urls = False
            elif line.startswith("source:"):
                source = line.split(":", 1)[1].strip()
                in_image_urls = False
            elif line.startswith("content_id:"):
                content_id = line.split(":", 1)[1].strip()
                in_image_urls = False
            elif line.startswith("image_urls:"):
                in_image_urls = True
                image_urls = []
            elif in_image_urls and line.strip().startswith("- "):
                image_urls.append(line.strip()[2:].strip())

    return {
        "title": title or Path(filepath).stem,
        "date": date_str,
        "source": source,
        "content_id": content_id,
        "body": body,
        "image_urls": image_urls,
    }


# ============ 同步到 Notion ============

def create_notion_page(title: str, date_str: str, parent_db_id: str) -> str:
    """在 Notion 数据库中创建页面"""
    data = {
        "parent": {"database_id": parent_db_id},
        "properties": {
            "Title": {
                "title": [{"text": {"content": title[:100]}}]
            },
        },
        "children": []
    }
    # 添加日期属性
    if date_str:
        data["properties"]["Date"] = {
            "date": {"start": date_str[:10]}
        }
    result = notion_post("pages", data)
    page_id = result["id"]
    logger.info(f"已创建 Notion 页面: {title} (ID: {page_id})")
    return page_id


def append_blocks(page_id: str, blocks: list, batch_size: int = 100):
    """分批追加 blocks（使用 PATCH）"""
    total = len(blocks)
    for start in range(0, total, batch_size):
        batch = blocks[start:start + batch_size]
        try:
            r = requests.patch(
                f"{NOTION_API}/blocks/{page_id}/children",
                headers=notion_headers(),
                json={"children": batch},
                timeout=60,
            )
            if r.status_code >= 400:
                logger.error(f"Notion API 错误: {r.status_code} {r.text[:500]}")
            r.raise_for_status()
            logger.info(f"  已追加 blocks {start+1}-{min(start+batch_size, total)}/{total}")
        except Exception as e:
            logger.error(f"  追加 blocks 失败 ({start+1}-{min(start+batch_size, total)}): {e}")
            for idx, block in enumerate(batch):
                try:
                    requests.patch(
                        f"{NOTION_API}/blocks/{page_id}/children",
                        headers=notion_headers(),
                        json={"children": [block]},
                        timeout=30,
                    ).raise_for_status()
                except Exception as e2:
                    bt = block.get("type", "unknown")
                    logger.error(f"    单个 block 失败 (idx={idx}, type={bt}): {e2}")
        time.sleep(0.4)


def sync_one_article(filepath: str, github_url_map: dict = None):
    """同步单篇文章到 Notion 数据库"""
    logger.info(f"正在同步: {filepath}")
    parsed = parse_md_file(filepath)
    title = parsed["title"]
    body = parsed["body"]
    date_str = parsed["date"]

    # 替换图片路径为 GitHub URL
    if github_url_map:
        for local_path, gh_url in github_url_map.items():
            body = body.replace(f"]({local_path})", f"]({gh_url})")

    # 移除第一个 # 标题行
    body = re.sub(r'^#\s+.+\n*', '', body)

    # 转换为 Notion blocks
    blocks = md_to_notion_blocks(body, github_url_map)
    if not blocks:
        logger.warning(f"文章内容为空，跳过: {title}")
        return None

    logger.info(f"  标题: {title}")
    logger.info(f"  内容: {len(blocks)} blocks")

    # 创建 Notion 数据库页面
    try:
        page_id = create_notion_page(title, date_str, NOTION_DATABASE_ID)
    except Exception as e:
        logger.error(f"创建页面失败: {e}")
        return None

    # 追加内容
    try:
        append_blocks(page_id, blocks)
    except Exception as e:
        logger.error(f"追加内容失败: {e}")

    logger.info(f"同步完成: {title}")
    return page_id


def sync_directory(dirpath: str):
    """同步整个目录下的文章到 Notion"""
    md_files = sorted(Path(dirpath).glob("**/*.md"))
    if not md_files:
        logger.warning(f"目录下没有 .md 文件: {dirpath}")
        return

    # 过滤掉非文章目录下的 .md 文件
    article_mds = []
    for md in md_files:
        # 文章目录结构: dirpath/article-name/article-name.md
        parent = md.parent
        if parent.name and md.name.startswith(parent.name.split("_", 1)[-1][:10] if "_" in parent.name else parent.name[:10]):
            article_mds.append(md)

    if not article_mds:
        article_mds = md_files

    logger.info(f"找到 {len(article_mds)} 篇文章待同步")
    for md_file in article_mds:
        try:
            # 构建 GitHub URL 映射（基于目录结构）
            article_dir = str(md_file.parent)
            github_map = build_github_url_map(article_dir)
            sync_one_article(str(md_file), github_map)
            time.sleep(1)
        except Exception as e:
            logger.error(f"同步失败 {md_file}: {e}")


def build_github_url_map(article_dir: str) -> dict:
    """
    从本地图片文件构建 GitHub URL 映射。
    需要在上传到 GitHub 后调用，此时图片已经在 GitHub 上了。
    """
    from github_uploader import GitHubUploader
    import json

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(config_path):
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    github_token = config.get("github_token", "")
    github_owner = config.get("github_owner", "")
    github_repo = config.get("github_repo", "red_ring_scraper")
    github_branch = config.get("github_branch", "main")

    if not github_token or not github_owner:
        return {}

    uploader = GitHubUploader(github_token, github_owner, github_repo, github_branch)

    # 计算 repo 路径
    # article_dir 类似 /path/to/articles/2026-05-02/my-article
    articles_base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "articles")
    rel_dir = os.path.relpath(article_dir, articles_base)
    # rel_dir 类似 2026-05-02/my-article

    url_map = {}
    images_dir = os.path.join(article_dir, "images")
    if os.path.isdir(images_dir):
        for filename in os.listdir(images_dir):
            if filename.startswith("."):
                continue
            repo_path = f"articles/{rel_dir}/images/{filename}"
            url_map[f"images/{filename}"] = uploader.get_raw_url(repo_path)

    other_dir = os.path.join(article_dir, "other-files")
    if os.path.isdir(other_dir):
        for filename in os.listdir(other_dir):
            if filename.startswith("."):
                continue
            repo_path = f"articles/{rel_dir}/other-files/{filename}"
            url_map[f"other-files/{filename}"] = uploader.get_raw_url(repo_path)

    return url_map


# ============ 主入口 ============

def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 notion_sync.py <markdown_file>        # 同步单篇文章")
        print("  python3 notion_sync.py <directory>            # 同步整个目录")
        print("  python3 notion_sync.py --verify               # 验证数据库连接")
        return

    target = sys.argv[1]

    if target == "--verify":
        if verify_database():
            print("数据库可访问!")
        return

    if not verify_database():
        print("请确保 Notion 数据库已共享给集成")
        return

    if os.path.isdir(target):
        sync_directory(target)
    elif os.path.isfile(target):
        github_map = build_github_url_map(str(Path(target).parent))
        sync_one_article(target, github_map)
    else:
        print(f"路径不存在: {target}")


if __name__ == "__main__":
    main()
