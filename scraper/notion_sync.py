"""
小红圈文章 → Notion 数据库同步脚本
将爬取的 Markdown 文章（含 GitHub 永久图片 URL）完整上传到 Notion 数据库

功能:
- 自动跳过"有声版"、"风险提示"等非正文文章（不上传到Notion，但GitHub仍保留）
- 自动识别文章中提及的投资标的，填入"提及标的"列
- 自动识别核心行业关键词，填入"Tag"列
- 智能标题处理：保留自带标题（如财经早餐），无标题时自动生成摘要标题

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
import sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import env_loader

# 非敏感配置(可选)
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config.json')
_config = {}
if os.path.exists(_config_path):
    with open(_config_path, 'r', encoding='utf-8') as _f:
        _config = json.load(_f)

NOTION_TOKEN = env_loader.get("NOTION_TOKEN")
NOTION_DATABASE_ID = env_loader.get("NOTION_DATABASE_ID")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ============ 股票映射与行业关键词 ============

_stock_mapping_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'stock_mapping.json')
_stock_mapping = {}
if os.path.exists(_stock_mapping_path):
    with open(_stock_mapping_path, 'r', encoding='utf-8') as _f:
        _stock_mapping = json.load(_f)

STOCK_NICKNAMES = _stock_mapping.get("nicknames", {})
INDUSTRY_KEYWORDS = _stock_mapping.get("industries", [])

# 需要跳过的文章类型关键词（出现在正文开头前几行即判定）
SKIP_KEYWORDS = ["有声版", "风险提示"]


# ============ 智能分析函数 ============

def should_skip_notion(parsed: dict) -> bool:
    """
    判断文章是否应该跳过Notion上传。
    规则：如果文章标题或正文前100字包含"有声版"、"风险提示"等关键词，则跳过。
    """
    title = parsed.get("title", "")
    body = parsed.get("body", "")
    # 检查正文前200字符（去除图片标记等）
    body_start = re.sub(r'!\[.*?\]\(.*?\)', '', body[:300]).strip()

    for keyword in SKIP_KEYWORDS:
        if keyword in title:
            return True
        if keyword in body_start:
            return True
    return False


def extract_mentioned_stocks(body: str) -> list:
    """
    从文章正文中识别提及的股票标的。
    返回去重的股票名称列表。
    """
    mentioned = set()
    # 按关键词长度降序匹配，避免短词误匹配
    sorted_nicknames = sorted(STOCK_NICKNAMES.keys(), key=len, reverse=True)

    for nickname in sorted_nicknames:
        if nickname in body:
            stock_names = STOCK_NICKNAMES[nickname]
            # 处理逗号分隔的多只股票（如"紫菜组合"）
            for name in stock_names.split(","):
                mentioned.add(name.strip())

    return sorted(mentioned)


def extract_industry_tags(body: str) -> list:
    """
    从文章正文中识别核心行业关键词。
    返回去重的行业标签列表。
    """
    tags = set()
    for keyword in INDUSTRY_KEYWORDS:
        if keyword in body:
            tags.add(keyword)
    return sorted(tags)


def generate_smart_title(parsed: dict) -> tuple:
    """
    智能标题处理：
    - 如果文章自带标题（如"财经早餐"），保留原标题，返回对应的tag
    - 如果文章没有标题（untitled_），则从正文中提取/生成摘要标题
    返回 (title, extra_tags)
    """
    title = parsed.get("title", "")
    body = parsed.get("body", "")
    extra_tags = []

    # 判断是否是自带标题的文章
    if "财经早餐" in title:
        extra_tags.append("财经早餐")
        return title, extra_tags

    # 检查正文中是否有明确的标题模式
    # 如正文第一行就是"YYYY年X月X日财经早餐"之类
    body_clean = re.sub(r'^#\s+.*\n*', '', body).strip()
    first_line = body_clean.split("\n")[0].strip() if body_clean else ""

    if "财经早餐" in first_line:
        extra_tags.append("财经早餐")
        # 从第一行提取标题
        date_title_match = re.match(r'(\d{4}年\d{1,2}月\d{1,2}日财经早餐)', first_line)
        if date_title_match:
            return date_title_match.group(1), extra_tags
        return first_line[:50], extra_tags

    # 如果是 untitled_ 类型，需要生成标题
    if title.startswith("untitled_"):
        # 从正文中提取摘要作为标题
        # 去除图片和分割线
        clean_text = re.sub(r'!\[.*?\]\(.*?\)', '', body_clean)
        clean_text = re.sub(r'[—]{3,}|---|\*\*\*|___', '', clean_text)
        clean_text = re.sub(r'\n{2,}', '\n', clean_text).strip()

        # 取前两段有意义的文本
        paragraphs = [p.strip() for p in clean_text.split("\n") if p.strip() and len(p.strip()) > 5]
        if paragraphs:
            # 用第一段的前50个字符作为标题
            summary = paragraphs[0][:50]
            # 去除末尾不完整的句子
            for sep in ["，", "。", "：", "；", "、", "！", "？"]:
                last_idx = summary.rfind(sep)
                if last_idx > 15:
                    summary = summary[:last_idx]
                    break
            return summary, extra_tags
        # 如果实在没有内容，用日期+序号
        date_str = parsed.get("date", "")[:10]
        content_id = parsed.get("content_id", "")
        return f"{date_str} 随笔 #{content_id[-4:]}" if content_id else title, extra_tags

    return title, extra_tags


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


def notion_patch(endpoint: str, data: dict):
    r = requests.patch(f"{NOTION_API}/{endpoint}", headers=notion_headers(), json=data, timeout=60)
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

def create_notion_page(title: str, date_str: str, parent_db_id: str,
                       mentioned_stocks: list = None, tags: list = None) -> str:
    """在 Notion 数据库中创建页面，包含提及标的和Tag"""
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
    # 添加提及标的（multi_select）
    if mentioned_stocks:
        data["properties"]["提及标的"] = {
            "multi_select": [{"name": stock[:100]} for stock in mentioned_stocks[:10]]
        }
    # 添加Tag（multi_select）
    if tags:
        data["properties"]["Tag"] = {
            "multi_select": [{"name": tag[:100]} for tag in tags[:10]]
        }

    result = notion_post("pages", data)
    page_id = result["id"]
    logger.info(f"已创建 Notion 页面: {title} (ID: {page_id})")
    if mentioned_stocks:
        logger.info(f"  提及标的: {mentioned_stocks}")
    if tags:
        logger.info(f"  Tags: {tags}")
    return page_id


def update_notion_page_properties(page_id: str, title: str = None,
                                  mentioned_stocks: list = None, tags: list = None):
    """更新已有 Notion 页面的属性（用于回溯更新）"""
    data = {"properties": {}}

    if title is not None:
        data["properties"]["Title"] = {
            "title": [{"text": {"content": title[:100]}}]
        }
    if mentioned_stocks is not None:
        data["properties"]["提及标的"] = {
            "multi_select": [{"name": stock[:100]} for stock in mentioned_stocks[:10]]
        }
    if tags is not None:
        data["properties"]["Tag"] = {
            "multi_select": [{"name": tag[:100]} for tag in tags[:10]]
        }

    if data["properties"]:
        notion_patch(f"pages/{page_id}", data)
        logger.info(f"已更新页面属性: {page_id}")


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

    # 检查是否应该跳过
    if should_skip_notion(parsed):
        logger.info(f"跳过文章（有声版/风险提示类）: {title}")
        return None

    # 智能标题处理
    smart_title, extra_tags = generate_smart_title(parsed)

    # 识别提及的股票标的
    mentioned_stocks = extract_mentioned_stocks(body)

    # 识别行业标签
    industry_tags = extract_industry_tags(body)

    # 合并所有 tags
    all_tags = list(set(extra_tags + industry_tags))

    # 替换图片路径为 GitHub URL
    if github_url_map:
        for local_path, gh_url in github_url_map.items():
            body = body.replace(f"]({local_path})", f"]({gh_url})")

    # 移除第一个 # 标题行
    body = re.sub(r'^#\s+.+\n*', '', body)

    # 转换为 Notion blocks
    blocks = md_to_notion_blocks(body, github_url_map)
    if not blocks:
        logger.warning(f"文章内容为空，跳过: {smart_title}")
        return None

    logger.info(f"  标题: {smart_title}")
    logger.info(f"  内容: {len(blocks)} blocks")

    # 创建 Notion 数据库页面（带标的和Tag）
    try:
        page_id = create_notion_page(
            smart_title, date_str, NOTION_DATABASE_ID,
            mentioned_stocks=mentioned_stocks if mentioned_stocks else None,
            tags=all_tags if all_tags else None,
        )
    except Exception as e:
        logger.error(f"创建页面失败: {e}")
        return None

    # 追加内容
    try:
        append_blocks(page_id, blocks)
    except Exception as e:
        logger.error(f"追加内容失败: {e}")

    logger.info(f"同步完成: {smart_title}")
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

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config.json')
    if not os.path.exists(config_path):
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    import env_loader

    github_token = env_loader.get("GITHUB_TOKEN")
    github_owner = env_loader.get("GITHUB_OWNER")
    github_repo = env_loader.get("GITHUB_REPO", "red_ring_scraper")
    github_branch = env_loader.get("GITHUB_BRANCH", "main")

    if not github_token or not github_owner:
        return {}

    uploader = GitHubUploader(github_token, github_owner, github_repo, github_branch)

    # 计算 repo 路径
    # article_dir 类似 /path/to/articles/2026-05-02/my-article
    articles_base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'articles')
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


# ============ 回溯更新已有 Notion 文章 ============

def retroactive_update_all():
    """
    回溯更新所有已有的 Notion 文章。
    基于本地文章内容，为每篇文章补充：提及标的、Tag、智能标题。
    """
    logger.info("开始回溯更新 Notion 文章...")

    # 获取 Notion 数据库中所有页面
    all_pages = []
    has_more = True
    start_cursor = None

    while has_more:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        result = notion_post(f"databases/{NOTION_DATABASE_ID}/query", params)
        all_pages.extend(result.get("results", []))
        has_more = result.get("has_more", False)
        start_cursor = result.get("next_cursor")
        time.sleep(0.5)

    logger.info(f"共获取 {len(all_pages)} 个 Notion 页面")

    # 构建本地文章索引 (content_id -> filepath)
    articles_base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'articles')
    local_articles = {}
    for md_file in Path(articles_base).glob("**/*.md"):
        parsed = parse_md_file(str(md_file))
        cid = parsed.get("content_id", "")
        if cid:
            local_articles[cid] = {"path": str(md_file), "parsed": parsed}

    updated_count = 0
    skipped_count = 0

    for page in all_pages:
        page_id = page["id"]
        props = page.get("properties", {})

        # 获取当前标题
        title_parts = props.get("Title", {}).get("title", [])
        current_title = "".join(t.get("plain_text", "") for t in title_parts)

        # 获取当前 tags 和 提及标的
        current_tags = [t["name"] for t in props.get("Tag", {}).get("multi_select", [])]
        current_stocks = [t["name"] for t in props.get("提及标的", {}).get("multi_select", [])]

        # 尝试通过标题匹配本地文章
        matched_parsed = None
        for cid, info in local_articles.items():
            p = info["parsed"]
            if p["title"] == current_title or current_title in p["title"] or p["title"] in current_title:
                matched_parsed = p
                break

        if not matched_parsed:
            # 通过日期+序号模式匹配
            # untitled_XXXXXXX 格式
            cid_match = re.search(r'untitled_(\d+)', current_title)
            if cid_match:
                cid_guess = cid_match.group(1)
                if cid_guess in local_articles:
                    matched_parsed = local_articles[cid_guess]["parsed"]

        if not matched_parsed:
            logger.info(f"无法匹配本地文章: {current_title}, 跳过")
            skipped_count += 1
            continue

        body = matched_parsed["body"]

        # 检查是否是需要跳过的文章类型
        if should_skip_notion(matched_parsed):
            logger.info(f"此文章为有声版/风险提示类，建议删除: {current_title} (page_id: {page_id})")
            skipped_count += 1
            continue

        # 计算新的属性值
        smart_title, extra_tags = generate_smart_title(matched_parsed)
        mentioned_stocks = extract_mentioned_stocks(body)
        industry_tags = extract_industry_tags(body)
        all_tags = list(set(extra_tags + industry_tags))

        # 确定是否需要更新
        needs_update = False
        new_title = None
        new_stocks = None
        new_tags = None

        # 更新标题（如果当前是 untitled_ 且我们有更好的标题）
        if current_title.startswith("untitled_") and smart_title != current_title:
            new_title = smart_title
            needs_update = True

        # 更新提及标的（如果有新发现且当前为空）
        if mentioned_stocks and not current_stocks:
            new_stocks = mentioned_stocks
            needs_update = True

        # 更新 Tag（合并新旧）
        merged_tags = list(set(current_tags + all_tags))
        if set(merged_tags) != set(current_tags) and merged_tags:
            new_tags = merged_tags
            needs_update = True

        if needs_update:
            try:
                update_notion_page_properties(
                    page_id,
                    title=new_title,
                    mentioned_stocks=new_stocks,
                    tags=new_tags,
                )
                updated_count += 1
                logger.info(f"  更新: {current_title} -> title={new_title}, stocks={new_stocks}, tags={new_tags}")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"  更新失败 {current_title}: {e}")
        else:
            skipped_count += 1

    logger.info(f"回溯更新完成: 更新 {updated_count} 篇, 跳过 {skipped_count} 篇")


# ============ 主入口 ============

def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 notion_sync.py <markdown_file>        # 同步单篇文章")
        print("  python3 notion_sync.py <directory>            # 同步整个目录")
        print("  python3 notion_sync.py --verify               # 验证数据库连接")
        print("  python3 notion_sync.py --retroactive          # 回溯更新所有已有文章")
        return

    target = sys.argv[1]

    if target == "--verify":
        if verify_database():
            print("数据库可访问!")
        return

    if target == "--retroactive":
        if not verify_database():
            print("请确保 Notion 数据库已共享给集成")
            return
        retroactive_update_all()
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
