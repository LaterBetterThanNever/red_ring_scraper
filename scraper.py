"""
小红圈文章爬虫 - 每日抓取文章并保存为 .md 文件
使用方法:
1. 在浏览器登录 https://www.red-ring.cn
2. 打开开发者工具 Network → 筛选 Fetch/XHR，找到请求域名为 api.redringvip.com 的接口
3. 在 Request Headers 中复制 access_token（与浏览器一致可同时配 accesstoken，脚本会同步设置）
4. 将 access_token 写入 config.json（见仓库内 config 示例）；Cookie 为可选（跨域 API 往往不带 Cookie）
5. 运行: python scraper.py
"""

import requests
import json
import os
import re
import time
import math
import logging
from datetime import datetime
from html import unescape
import urllib.parse

# ============ 配置 ============
BASE_URL = "https://www.red-ring.cn"
# 实际接口多在独立域名，与页面 www.red-ring.cn 跨域；鉴权通常为请求头 access_token，而非 Cookie
DEFAULT_API_BASE = "https://api.redringvip.com/api"
GROUP_ID = 27593  # 圈子 ID（URL 里的 gid），与 plate_id（板块 tab，多为 1）不同
DEFAULT_PLATE_ID = 1  # 默认「全部」等板块，见前端 /content/plate/get；勿把 group_id 当成 plate_id
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "articles")
LOG_FILE = os.path.join(os.path.dirname(__file__), "scraper.log")
RECORD_FILE = os.path.join(os.path.dirname(__file__), "scraped_ids.json")

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def load_config():
    """加载配置文件"""
    if not os.path.exists(CONFIG_FILE):
        # 创建模板配置
        template = {
            "access_token": "在 Network 里 api.redringvip.com 请求的 Request Headers 中复制 access_token",
            "cookie": "",
            "api_base": DEFAULT_API_BASE,
            "group_id": GROUP_ID,
            "plate_id": DEFAULT_PLATE_ID,
            "page_size": 20,
            "yyyymm": "",
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "download_images": True,
            "download_attachments": False,
            "include_comments": False,
            "max_comments_per_article": 50
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
        logger.error(
            f"请先编辑配置文件: {CONFIG_FILE}，填入 access_token（推荐）或有效 cookie"
        )
        raise SystemExit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_scraped_ids():
    """加载已爬取的文章 ID 列表"""
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_scraped_ids(ids: set):
    """保存已爬取的文章 ID 列表"""
    with open(RECORD_FILE, "w") as f:
        json.dump(list(ids), f)


class RedRingScraper:
    def __init__(self, config: dict):
        self.api_base = (config.get("api_base") or DEFAULT_API_BASE).rstrip("/")
        self.session = requests.Session()
        ua = config.get(
            "user_agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        headers = {
            "User-Agent": ua,
            "Referer": f"{BASE_URL}/",
            "Accept": "application/json, text/plain, */*",
            "Origin": BASE_URL,
        }
        token = (config.get("access_token") or config.get("accessToken") or "").strip()
        if token and "复制" not in token:
            # 与浏览器一致：站点同时发送 access_token 与 accesstoken
            headers["access_token"] = token
            headers["accesstoken"] = token
        cookie = (config.get("cookie") or "").strip()
        if cookie and "粘贴" not in cookie:
            headers["Cookie"] = cookie
        self.session.headers.update(headers)
        self.group_id = int(config.get("group_id", GROUP_ID))
        self.plate_id = int(config.get("plate_id", config.get("plateId", DEFAULT_PLATE_ID)))
        self.page_size = int(config.get("page_size", 20))
        yyyymm = (config.get("yyyymm") or "").strip()
        self.yyyymm = yyyymm if yyyymm else None
        self.download_images = config.get("download_images", True)
        self.download_attachments = config.get("download_attachments", False)
        self.include_comments = config.get("include_comments", False)
        self.max_comments_per_article = config.get("max_comments_per_article", 50)
        self.scraped_ids = load_scraped_ids()
        if not token and not cookie:
            logger.error(
                "config.json 未配置 access_token 或 cookie；跨域 API 一般只需从 Network 复制 access_token"
            )
            raise SystemExit(1)

    @staticmethod
    def _api_ok(data: dict) -> bool:
        """api.redringvip.com 成功多为 status \"1\"；部分旧接口用 code。"""
        if not data:
            return False
        st = data.get("status")
        if st in (1, "1"):
            return True
        code = data.get("code")
        if code in (0, 200, "0", "200"):
            return True
        return False

    @staticmethod
    def _unwrap_data_field(data_field):
        """接口里 data 常为 JSON 字符串。"""
        if data_field is None or data_field == "":
            return None
        if isinstance(data_field, str):
            try:
                return json.loads(data_field)
            except json.JSONDecodeError:
                return None
        return data_field

    def _api_get(self, endpoint: str, params: dict = None) -> dict:
        """调用 API GET 请求"""
        url = f"{self.api_base}/{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            info = (data.get("info") or data.get("message") or "").strip()
            if "请登录" in info or str(data.get("status")) == "9":
                logger.error(
                    "API 要求登录：请在 config.json 填写有效的 access_token（"
                    "Network 里 **api.redringvip.com** 请求的 Request Headers 中，与浏览器一致可同时需要 "
                    "access_token / accesstoken；跨域请求通常没有 Cookie 属正常）。"
                )
            elif not self._api_ok(data):
                logger.warning(f"API 返回异常: {url} -> {data}")
            return data
        except requests.RequestException as e:
            logger.error(f"请求失败: {url} -> {e}")
            return {}

    def fetch_list_page(self, index: int, last_time=None):
        """
        获取一页帖子列表（与网页一致：index 从 1 递增；index>1 时需带上一页末条 lastTime）。
        返回 (items, next_last_time, total_page)。
        """
        params = {
            "gid": self.group_id,
            "plateId": self.plate_id,
            "type": 1,
            "index": index,
            "pageSize": self.page_size,
        }
        if self.yyyymm:
            params["yyyymm"] = self.yyyymm
        if index > 1 and last_time is not None:
            params["lastTime"] = int(math.ceil(float(last_time)))

        data = self._api_get("content/list/get", params)
        if not data or not self._api_ok(data):
            return [], None, None

        payload = self._unwrap_data_field(data.get("data"))
        if not isinstance(payload, dict):
            logger.warning("列表接口 data 无法解析为对象")
            return [], None, None

        items = payload.get("list") or []
        page_meta = payload.get("page") or {}
        total_page = page_meta.get("total_page") or page_meta.get("totalPage")
        next_cursor = items[-1]["lastTime"] if items else None
        if items:
            logger.info(
                f"列表第 {index} 页: plateId={self.plate_id}, 本页 {len(items)} 条"
                + (f", 总页数约 {total_page}" if total_page else "")
            )
        return items, next_cursor, total_page

    def get_article_detail(self, content_id: int) -> dict:
        """获取单篇文章详情"""
        data = self._api_get(
            "content/detail/get",
            {"gid": self.group_id, "cid": content_id},
        )
        if data and self._api_ok(data):
            body = self._unwrap_data_field(data.get("data", data.get("result")))
            return body if isinstance(body, dict) else {}
        return {}

    def get_article_comments(self, content_id: int, page: int = 1, page_size: int = 20) -> dict:
        """获取文章评论"""
        params = {
            "gid": self.group_id,
            "cid": content_id,
            "page": page,
            "pageSize": page_size,
        }
        data = self._api_get("content/comment/get", params)
        if data and self._api_ok(data):
            payload = self._unwrap_data_field(data.get("data"))
            return payload if isinstance(payload, dict) else {}
        return {}

    def download_image(self, img_url: str, content_id: int, index: int = 0, article_dir: str = None) -> str:
        """下载图片并保存到本地（文章目录下的 images/ 子目录）"""
        try:
            # 确定图片保存目录
            if article_dir:
                images_dir = os.path.join(article_dir, "images")
            else:
                images_dir = os.path.join(OUTPUT_DIR, "images")
            os.makedirs(images_dir, exist_ok=True)
            
            # 解析URL获取文件名
            parsed_url = urllib.parse.urlparse(img_url)
            filename = os.path.basename(parsed_url.path)
            # 去掉 URL query 参数（如 ?e=...&token=...）
            if '?' in filename:
                filename = filename.split('?')[0]
            if not filename or '.' not in filename:
                filename = f"image_{content_id}_{index}.jpg"
            
            # 生成安全文件名
            safe_filename = re.sub(r'[\\/:*?"<>|]', "_", filename)
            filepath = os.path.join(images_dir, safe_filename)
            
            # 如果文件已存在，跳过下载
            if os.path.exists(filepath):
                logger.info(f"图片已存在，跳过: {safe_filename}")
                if article_dir:
                    return f"images/{safe_filename}"
                return f"images/{safe_filename}"
            
            # 下载图片
            response = self.session.get(img_url, timeout=30)
            response.raise_for_status()
            
            # 保存图片
            with open(filepath, "wb") as f:
                f.write(response.content)
            
            logger.info(f"已下载图片: {filepath}")
            return f"images/{safe_filename}"
        except Exception as e:
            logger.error(f"下载图片失败 {img_url}: {e}")
            return img_url

    def download_attachment(self, attachment_url: str, content_id: int, filename: str = None, article_dir: str = None) -> str:
        """下载附件并保存到本地（文章目录下的 other-files/ 子目录）"""
        try:
            # 确定附件保存目录
            if article_dir:
                attachments_dir = os.path.join(article_dir, "other-files")
            else:
                attachments_dir = os.path.join(OUTPUT_DIR, "attachments")
            os.makedirs(attachments_dir, exist_ok=True)
            
            # 解析URL获取文件名
            if not filename:
                parsed_url = urllib.parse.urlparse(attachment_url)
                filename = os.path.basename(parsed_url.path)
                if not filename or '.' not in filename:
                    filename = f"attachment_{content_id}.bin"
            
            # 生成唯一文件名
            safe_filename = re.sub(r'[\\/:*?"<>|]', "_", filename)
            filepath = os.path.join(attachments_dir, safe_filename)
            
            # 下载附件
            response = self.session.get(attachment_url, timeout=30)
            response.raise_for_status()
            
            # 保存附件
            with open(filepath, "wb") as f:
                f.write(response.content)
            
            logger.info(f"已下载附件: {filepath}")
            if article_dir:
                return f"other-files/{safe_filename}"
            return f"attachments/{safe_filename}"
        except Exception as e:
            logger.error(f"下载附件失败 {attachment_url}: {e}")
            return attachment_url

    def extract_attachments(self, article: dict) -> list:
        """从文章中提取附件信息"""
        attachments = []
        
        # 检查常见的附件字段
        if "attachments" in article and isinstance(article["attachments"], list):
            for att in article["attachments"]:
                if isinstance(att, dict) and "url" in att and "name" in att:
                    attachments.append({
                        "url": att["url"],
                        "name": att["name"]
                    })
        
        # 检查其他可能的附件字段
        for key in ["files", "fileList", "mediaList", "resources"]:
            if key in article and isinstance(article[key], list):
                for item in article[key]:
                    if isinstance(item, dict):
                        url = item.get("url") or item.get("fileUrl") or item.get("src")
                        name = item.get("name") or item.get("fileName") or item.get("title")
                        if url and name:
                            attachments.append({"url": url, "name": name})
        
        return attachments

    def add_attachments_to_markdown(self, md_content: str, attachments: list, content_id: int) -> str:
        """在Markdown内容中添加附件链接"""
        if not attachments:
            return md_content
        
        attachments_section = "\n## 附件\n\n"
        for i, att in enumerate(attachments):
            try:
                local_path = self.download_attachment(att["url"], content_id, att["name"])
                attachments_section += f"- [{att['name']}]({local_path})\n"
            except Exception as e:
                logger.error(f"添加附件链接失败 {att['name']}: {e}")
                attachments_section += f"- [{att['name']}]({att['url']})\n"
        
        return md_content + attachments_section

    def add_comments_to_markdown(self, md_content: str, content_id: int, max_comments: int = 50) -> str:
        """在Markdown内容中添加评论"""
        if not content_id:
            return md_content
        
        try:
            # 获取评论
            comments_data = self.get_article_comments(content_id)
            if not comments_data:
                return md_content
            
            # 提取评论列表
            comments = comments_data.get("list") or []
            if not comments:
                return md_content
            
            # 限制评论数量
            comments = comments[:max_comments]
            
            if not comments:
                return md_content
            
            comments_section = "\n## 评论\n\n"
            for comment in comments:
                author = comment.get("author", {}).get("nickname", "匿名用户")
                content = comment.get("content", "")
                create_time = comment.get("createTime") or comment.get("ctime") or ""
                
                if isinstance(create_time, (int, float)):
                    ts = float(create_time)
                    if ts > 1e12:
                        ts = ts / 1000
                    create_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                
                comments_section += f"### {author} ({create_time})\n{content}\n\n"
            
            return md_content + comments_section
        except Exception as e:
            logger.error(f"添加评论失败: {e}")
            return md_content

    def html_to_markdown(self, html_content: str, title: str = "", content_id: int = None, article_dir: str = None) -> str:
        """将 HTML 内容简单转换为 Markdown，支持图片下载"""
        if not html_content:
            return ""

        text = html_content

        # 处理常见 HTML 标签
        # 标题
        for i in range(6, 0, -1):
            text = re.sub(
                rf"<h{i}[^>]*>(.*?)</h{i}>",
                lambda m, level=i: f"\n{'#' * level} {m.group(1).strip()}\n",
                text,
                flags=re.DOTALL,
            )

        # 图片 - 收集所有图片URL，下载到文章目录，Markdown中使用相对路径
        img_urls = []
        img_pattern = r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>'
        for match in re.finditer(img_pattern, text):
            img_url = match.group(1)
            if img_url and img_url.startswith(('http://', 'https://')):
                img_urls.append(img_url)
        
        # 替换图片标签
        # 下载图片到文章的 images/ 目录，Markdown 中使用相对路径 images/xxx.jpg
        # 同时保留原始公网 URL 在 front matter 的 image_urls 字段，供 Notion 同步时使用
        for i, img_url in enumerate(img_urls):
            if self.download_images and content_id is not None:
                local_path = self.download_image(img_url, content_id, i, article_dir=article_dir)
                # Markdown 中使用相对路径
                text = re.sub(
                    rf'<img[^>]*src=["\']{re.escape(img_url)}["\'][^>]*>',
                    f"\n![image]({local_path})\n",
                    text,
                    count=1
                )
            else:
                text = re.sub(
                    rf'<img[^>]*src=["\']{re.escape(img_url)}["\'][^>]*>',
                    f"\n![image]({img_url})\n",
                    text,
                    count=1
                )

        # 链接
        text = re.sub(
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            r"[\2](\1)",
            text,
            flags=re.DOTALL,
        )

        # 粗体
        text = re.sub(r"<(strong|b)>(.*?)</\1>", r"**\2**", text, flags=re.DOTALL)

        # 斜体
        text = re.sub(r"<(em|i)>(.*?)</\1>", r"*\2*", text, flags=re.DOTALL)

        # 列表项
        text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.DOTALL)

        # 段落和换行
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\1\n", text, flags=re.DOTALL)
        text = re.sub(r"<div[^>]*>(.*?)</div>", r"\n\1\n", text, flags=re.DOTALL)

        # 引用块
        text = re.sub(
            r"<blockquote[^>]*>(.*?)</blockquote>",
            lambda m: "\n" + "\n".join(f"> {line}" for line in m.group(1).strip().split("\n")) + "\n",
            text,
            flags=re.DOTALL,
        )

        # 删除剩余 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)

        # HTML 实体解码
        text = unescape(text)

        # 清理多余空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        # 加上标题
        if title:
            text = f"# {title}\n\n{text}"

        return text

    def save_article(self, article: dict) -> bool:
        """将文章保存为 .md 文件（新目录结构：每篇文章独立文件夹）"""
        content_id = article.get("contentId") or article.get("cid") or article.get("id")
        title = article.get("title") or article.get("subject") or f"untitled_{content_id}"
        body = article.get("content") or article.get("body") or article.get("text") or ""
        create_time = (
            article.get("createTime")
            or article.get("createdAt")
            or article.get("ctime")
            or article.get("time")
            or ""
        )

        # 处理时间
        if isinstance(create_time, str) and create_time.isdigit():
            create_time = int(create_time)
        if isinstance(create_time, (int, float)):
            ts = float(create_time)
            if ts > 1e12:
                ts = ts / 1000
            date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(create_time, str) and create_time:
            date_str = create_time[:10]
            time_str = create_time
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 清理文件名中的非法字符，用作文件夹名和文件名
        safe_title = re.sub(r'[\\/:*?"<>|#]', "_", title)
        safe_title = safe_title.strip(". ")[:80]  # 限制长度

        # 新目录结构: articles/2026-05-02/my-article/my-article.md
        # 文章目录名 = date + title slug
        article_slug = f"{date_str}_{safe_title}"
        date_dir = os.path.join(OUTPUT_DIR, date_str)
        article_dir = os.path.join(date_dir, article_slug)
        os.makedirs(article_dir, exist_ok=True)

        filename = f"{article_slug}.md"
        filepath = os.path.join(article_dir, filename)

        # 收集原始图片 URL（用于 front matter，供 Notion 同步时使用）
        img_urls = []
        img_pattern = r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>'
        for match in re.finditer(img_pattern, body):
            img_url = match.group(1)
            if img_url and img_url.startswith(('http://', 'https://')):
                img_urls.append(img_url)

        # 转换为 Markdown（图片下载到 article_dir/images/）
        md_content = self.html_to_markdown(body, title, content_id, article_dir=article_dir)
        
        # 添加附件（下载到 article_dir/other-files/）
        if self.download_attachments:
            attachments = self.extract_attachments(article)
            if attachments:
                md_content = self.add_attachments_to_markdown(md_content, attachments, content_id)
        
        # 添加评论
        if self.include_comments:
            md_content = self.add_comments_to_markdown(md_content, content_id, self.max_comments_per_article)

        # 添加元信息（包含原始图片URL列表）
        image_urls_yaml = ""
        if img_urls:
            image_urls_yaml = "\nimage_urls:\n" + "\n".join(f"  - {url}" for url in img_urls)
        
        metadata = f"""---
title: "{title}"
date: {time_str}
source: {BASE_URL}/post/{self.group_id}-{content_id}
content_id: {content_id}{image_urls_yaml}
---

"""
        full_content = metadata + md_content

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_content)

        logger.info(f"已保存: {filepath}")
        return True

    def scrape_latest(self, max_pages: int = 5):
        """抓取最新文章"""
        logger.info(
            f"开始抓取圈子 gid={self.group_id}, plate_id={self.plate_id} 的最新文章..."
        )
        new_count = 0
        list_index = 1
        last_time = None

        for _ in range(max_pages):
            logger.info(f"正在获取列表 index={list_index} ...")
            articles, next_last, total_page = self.fetch_list_page(list_index, last_time)

            if not articles:
                logger.info("本页无内容，停止翻页")
                break

            all_old = True
            for item in articles:
                cid = str(item.get("contentId") or item.get("cid") or item.get("id", ""))
                if not cid:
                    continue

                if cid in self.scraped_ids:
                    logger.info(f"文章 {cid} 已抓取过，跳过")
                    continue

                all_old = False
                # 获取文章详情
                detail = self.get_article_detail(int(cid))
                if detail:
                    if not detail.get("title") and item.get("title"):
                        detail = {**detail, "title": item["title"]}
                    if self.save_article(detail):
                        self.scraped_ids.add(cid)
                        new_count += 1
                else:
                    # 如果详情接口没数据，尝试用列表数据
                    if item.get("content") or item.get("body"):
                        if self.save_article(item):
                            self.scraped_ids.add(cid)
                            new_count += 1

                # 请求间隔，避免被封
                time.sleep(2)

            if all_old:
                logger.info("本页所有文章均已抓取过，停止")
                break

            list_index += 1
            last_time = next_last
            if total_page is not None and list_index > int(total_page):
                logger.info("已到最后一页")
                break

            time.sleep(3)

        save_scraped_ids(self.scraped_ids)
        logger.info(f"本次抓取完成，新增 {new_count} 篇文章")

    def scrape_all(self):
        """一次性爬取所有文章（从第一页开始，直到没有新文章）"""
        logger.info(
            f"开始一次性爬取圈子 gid={self.group_id}, plate_id={self.plate_id} 的所有文章..."
        )
        new_count = 0
        list_index = 1
        last_time = None

        while True:
            logger.info(f"正在获取列表 index={list_index} ...")
            articles, next_last, total_page = self.fetch_list_page(list_index, last_time)

            if not articles:
                logger.info("本页无内容，停止翻页")
                break

            all_old = True
            for item in articles:
                cid = str(item.get("contentId") or item.get("cid") or item.get("id", ""))
                if not cid:
                    continue

                if cid in self.scraped_ids:
                    logger.info(f"文章 {cid} 已抓取过，跳过")
                    continue

                all_old = False
                # 获取文章详情
                detail = self.get_article_detail(int(cid))
                if detail:
                    if not detail.get("title") and item.get("title"):
                        detail = {**detail, "title": item["title"]}
                    if self.save_article(detail):
                        self.scraped_ids.add(cid)
                        new_count += 1
                else:
                    # 如果详情接口没数据，尝试用列表数据
                    if item.get("content") or item.get("body"):
                        if self.save_article(item):
                            self.scraped_ids.add(cid)
                            new_count += 1

                # 请求间隔，避免被封
                time.sleep(2)

            if all_old:
                logger.info("本页所有文章均已抓取过，停止")
                break

            list_index += 1
            last_time = next_last
            if total_page is not None and list_index > int(total_page):
                logger.info("已到最后一页")
                break

            time.sleep(3)

        save_scraped_ids(self.scraped_ids)
        logger.info(f"一次性爬取完成，新增 {new_count} 篇文章")

    def scrape_by_date(self, target_date: str):
        """按指定日期爬取当天发布的所有文章"""
        logger.info(
            f"开始爬取 {target_date} 发布的文章..."
        )
        
        # 临时设置 yyyymm 参数
        original_yyyymm = self.yyyymm
        try:
            # 解析日期
            from datetime import datetime
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            yyyymm = dt.strftime("%Y%m")
            self.yyyymm = yyyymm
            
            # 抓取当天文章
            new_count = 0
            list_index = 1
            last_time = None
            
            while True:
                logger.info(f"正在获取列表 index={list_index} ...")
                articles, next_last, total_page = self.fetch_list_page(list_index, last_time)
                
                if not articles:
                    logger.info("本页无内容，停止翻页")
                    break
                
                # 标记是否有新文章被处理
                has_new_articles = False
                for item in articles:
                    # 检查文章发布时间是否为指定日期
                    # 尝试多种时间字段
                    time_fields = ["time", "createTime", "ctime", "publishTime", "postTime"]
                    create_time = None
                    for field in time_fields:
                        if field in item:
                            create_time = item[field]
                            break
                    
                    if create_time is None:
                        article_date = ""
                    elif isinstance(create_time, (int, float)):
                        ts = float(create_time)
                        if ts > 1e12:
                            ts = ts / 1000
                        try:
                            article_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                        except (ValueError, OSError):
                            article_date = ""
                    elif isinstance(create_time, str) and create_time.isdigit() and len(create_time) >= 10:
                        try:
                            ts = float(create_time)
                            if ts > 1e12:
                                ts = ts / 1000
                            article_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                        except (ValueError, OSError):
                            article_date = ""
                    else:
                        article_date = ""
                    
                    if article_date != target_date:
                        continue
                    
                    cid = str(item.get("contentId") or item.get("cid") or item.get("id", ""))
                    if not cid:
                        continue
                    
                    # 检查是否已抓取过（使用全局记录）
                    if cid in self.scraped_ids:
                        logger.info(f"文章 {cid} 已抓取过，跳过")
                        continue
                    
                    has_new_articles = True
                    # 获取文章详情
                    detail = self.get_article_detail(int(cid))
                    if detail:
                        if not detail.get("title") and item.get("title"):
                            detail = {**detail, "title": item["title"]}
                        if self.save_article(detail):
                            self.scraped_ids.add(cid)
                            new_count += 1
                    else:
                        # 如果详情接口没数据，尝试用列表数据
                        if item.get("content") or item.get("body"):
                            if self.save_article(item):
                                self.scraped_ids.add(cid)
                                new_count += 1
                    
                    # 请求间隔，避免被封
                    time.sleep(2)
                
                if not has_new_articles:
                    logger.info("本页没有符合条件的新文章，停止")
                    break
                
                list_index += 1
                last_time = next_last
                if total_page is not None and list_index > int(total_page):
                    logger.info("已到最后一页")
                    break
                
                time.sleep(3)
            
            # 保存已抓取的ID
            save_scraped_ids(self.scraped_ids)
            
            logger.info(f"{target_date} 爬取完成，新增 {new_count} 篇文章")
            
        finally:
            # 恢复原始 yyyymm
            self.yyyymm = original_yyyymm

    def scrape_single(self, content_id: int):
        """抓取单篇文章"""
        logger.info(f"抓取单篇文章: {content_id}")
        detail = self.get_article_detail(content_id)
        if detail:
            self.save_article(detail)
            self.scraped_ids.add(str(content_id))
            save_scraped_ids(self.scraped_ids)
        else:
            logger.error(f"无法获取文章 {content_id} 的内容")


def _sync_to_notion(args):
    """爬取后自动同步到 Notion 数据库"""
    try:
        from notion_sync import sync_one_article, sync_directory, verify_database
        
        logger.info("开始同步文章到 Notion 数据库...")
        
        # 验证 Notion 连接
        if not verify_database():
            logger.error("无法连接 Notion 数据库，请检查集成权限设置")
            return
        
        # 确定要同步的目录或文件
        if args.date:
            target_dir = os.path.join(OUTPUT_DIR, args.date)
            if os.path.isdir(target_dir):
                sync_directory(target_dir)
            else:
                logger.warning(f"日期目录不存在: {target_dir}")
        elif args.single:
            # 查找单篇文章的文件
            found = False
            for root, dirs, files in os.walk(OUTPUT_DIR):
                for f in files:
                    if not f.endswith('.md'):
                        continue
                    filepath = os.path.join(root, f)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as fh:
                            content = fh.read(500)
                        if f'content_id: {args.single}' in content or f'content_id:{args.single}' in content:
                            sync_one_article(filepath)
                            found = True
                            break
                    except Exception:
                        pass
                if found:
                    break
            if not found:
                logger.warning(f"未找到 content_id 为 {args.single} 的文章")
        else:
            # 同步整个 articles 目录
            for date_dir in sorted(Path(OUTPUT_DIR).iterdir()):
                if date_dir.is_dir() and date_dir.name != 'images' and date_dir.name != 'attachments':
                    sync_directory(str(date_dir))
        
        logger.info("Notion 同步完成!")
    except ImportError:
        logger.error("找不到 notion_sync.py，请确保它与 scraper.py 在同一目录")
    except Exception as e:
        logger.error(f"同步到 Notion 失败: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="小红圈文章爬虫")
    parser.add_argument("--single", type=int, help="抓取单篇文章，传入 content_id")
    parser.add_argument("--pages", type=int, default=5, help="最多翻页数 (默认 5)")
    parser.add_argument("--group", type=int, help="指定圈子 ID")
    parser.add_argument("--date", type=str, help="按日期爬取，格式如 2026-04-01")
    parser.add_argument("--all", action="store_true", help="一次性爬取所有文章（从第一页开始）")
    parser.add_argument("--comments", action="store_true", help="包含评论（默认不包含）")
    parser.add_argument("--attachments", action="store_true", help="包含附件（默认不包含）")
    parser.add_argument("--notion", action="store_true", help="爬取后自动同步到 Notion")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    config = load_config()

    if args.group:
        config["group_id"] = args.group

    scraper = RedRingScraper(config)

    if args.single:
        scraper.scrape_single(args.single)
    elif args.all:
        # 一次性爬取所有文章
        scraper.scrape_all()
    elif args.date:
        # 按日期爬取
        scraper.scrape_by_date(args.date)
    else:
        scraper.scrape_latest(max_pages=args.pages)

    # 爬取完成后，如果指定了 --notion，自动同步到 Notion
    if args.notion:
        _sync_to_notion(args)


if __name__ == "__main__":
    main()
