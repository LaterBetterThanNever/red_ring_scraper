"""
GitHub 文件上传模块
将文章和图片上传到 GitHub 仓库，返回永久可访问的 raw URL

使用方法:
  from github_uploader import GitHubUploader
  uploader = GitHubUploader(token="ghp_xxx", owner="username", repo="repo-name")
  url = uploader.upload_file("articles/2026-05-02/my-article/images/cover.png")
  # url = "https://raw.githubusercontent.com/username/repo-name/main/articles/2026-05-02/my-article/images/cover.png"
"""

import os
import base64
import logging
import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubUploader:
    def __init__(self, token: str, owner: str, repo: str, branch: str = "main"):
        """
        token: GitHub Personal Access Token (需要 repo 权限)
        owner: GitHub 用户名
        repo:  仓库名
        branch: 分支名，默认 main
        """
        self.token = token
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _api_put(self, endpoint: str, data: dict):
        r = requests.put(f"{GITHUB_API}/{endpoint}", headers=self.headers, json=data, timeout=60)
        if r.status_code >= 400:
            logger.error(f"GitHub API 错误: {r.status_code} {r.text[:300]}")
        r.raise_for_status()
        return r.json()

    def _api_post(self, endpoint: str, data: dict):
        r = requests.post(f"{GITHUB_API}/{endpoint}", headers=self.headers, json=data, timeout=60)
        if r.status_code >= 400:
            logger.error(f"GitHub API 错误: {r.status_code} {r.text[:300]}")
        r.raise_for_status()
        return r.json()

    def create_repo(self, private: bool = True):
        """创建 GitHub 仓库（如果不存在）"""
        data = {
            "name": self.repo,
            "description": "小红圈文章图片存储",
            "private": private,
            "auto_init": True,
        }
        try:
            result = self._api_post("user/repos", data)
            logger.info(f"已创建仓库: {result.get('html_url')}")
            return result
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 422:
                logger.info(f"仓库已存在: {self.owner}/{self.repo}")
                return self.get_repo_info()
            raise

    def get_repo_info(self):
        """获取仓库信息"""
        r = requests.get(f"{GITHUB_API}/repos/{self.owner}/{self.repo}", headers=self.headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def upload_file(self, local_path: str, repo_path: str = None, message: str = None) -> str:
        """
        上传单个文件到 GitHub 仓库

        local_path: 本地文件路径
        repo_path:  仓库中的路径（默认与 local_path 相同）
        message:    commit 消息

        返回: raw.githubusercontent.com 的永久 URL
        """
        if not os.path.exists(local_path):
            logger.error(f"文件不存在: {local_path}")
            return ""

        if repo_path is None:
            repo_path = local_path.lstrip("./")

        if message is None:
            message = f"upload {repo_path}"

        # 读取文件内容
        with open(local_path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")

        # 检查文件是否已存在（获取 SHA）
        sha = None
        try:
            r = requests.get(
                f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{repo_path}",
                headers=self.headers,
                params={"ref": self.branch},
                timeout=30,
            )
            if r.status_code == 200:
                sha = r.json().get("sha")
        except Exception:
            pass

        # 上传/更新文件
        data = {
            "message": message,
            "content": content,
            "branch": self.branch,
        }
        if sha:
            data["sha"] = sha

        result = self._api_put(
            f"repos/{self.owner}/{self.repo}/contents/{repo_path}",
            data,
        )

        raw_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/{repo_path}"
        logger.info(f"已上传: {repo_path}")
        return raw_url

    def upload_directory(self, local_dir: str, repo_base: str = "", message: str = None) -> dict:
        """
        上传整个目录到 GitHub 仓库

        local_dir:  本地目录路径
        repo_base:  仓库中的基础路径
        message:    commit 消息前缀

        返回: {相对路径: raw URL} 的映射
        """
        url_map = {}
        if not os.path.isdir(local_dir):
            logger.error(f"目录不存在: {local_dir}")
            return url_map

        for root, dirs, files in os.walk(local_dir):
            for filename in files:
                # 跳过隐藏文件和 .md 文件（只上传图片和附件）
                if filename.startswith(".") or filename.endswith(".md"):
                    continue

                local_path = os.path.join(root, filename)
                # 计算仓库中的相对路径
                rel_path = os.path.relpath(local_dir, local_path)
                repo_path = os.path.join(repo_base, os.path.relpath(local_path, local_dir))

                if message:
                    msg = f"{message}: {os.path.relpath(local_path, local_dir)}"
                else:
                    msg = f"upload {repo_path}"

                try:
                    raw_url = self.upload_file(local_path, repo_path, msg)
                    # 记录本地相对路径 → raw URL 的映射
                    local_rel = os.path.relpath(local_path, local_dir)
                    url_map[local_rel] = raw_url
                except Exception as e:
                    logger.error(f"上传失败 {local_path}: {e}")

        return url_map

    def upload_article(self, article_dir: str, date_str: str) -> dict:
        """
        上传单篇文章的所有图片到 GitHub

        article_dir: 文章目录路径 (如 articles/2026-05-02/my-article/)
        date_str:    日期字符串 (如 2026-05-02)

        返回: {本地相对路径: GitHub raw URL} 的映射
        """
        article_name = os.path.basename(article_dir)
        repo_base = f"articles/{date_str}/{article_name}"

        url_map = {}
        images_dir = os.path.join(article_dir, "images")
        if os.path.isdir(images_dir):
            for filename in os.listdir(images_dir):
                if filename.startswith("."):
                    continue
                local_path = os.path.join(images_dir, filename)
                repo_path = f"{repo_base}/images/{filename}"
                try:
                    raw_url = self.upload_file(
                        local_path, repo_path,
                        message=f"upload image: {article_name}/images/{filename}"
                    )
                    url_map[f"images/{filename}"] = raw_url
                except Exception as e:
                    logger.error(f"上传图片失败 {filename}: {e}")

        other_dir = os.path.join(article_dir, "other-files")
        if os.path.isdir(other_dir):
            for filename in os.listdir(other_dir):
                if filename.startswith("."):
                    continue
                local_path = os.path.join(other_dir, filename)
                repo_path = f"{repo_base}/other-files/{filename}"
                try:
                    raw_url = self.upload_file(
                        local_path, repo_path,
                        message=f"upload attachment: {article_name}/other-files/{filename}"
                    )
                    url_map[f"other-files/{filename}"] = raw_url
                except Exception as e:
                    logger.error(f"上传附件失败 {filename}: {e}")

        return url_map

    def get_raw_url(self, repo_path: str) -> str:
        """获取文件的 raw URL（不上传，只计算 URL）"""
        return f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/{repo_path}"


def create_uploader_from_config(config: dict) -> GitHubUploader:
    """从配置字典创建 GitHubUploader 实例"""
    return GitHubUploader(
        token=config.get("github_token", ""),
        owner=config.get("github_owner", ""),
        repo=config.get("github_repo", "red-ring-images"),
        branch=config.get("github_branch", "main"),
    )
