"""NovelForge 安装配置"""

from setuptools import setup, find_packages

setup(
    name="novelforge",
    version="0.1.0",
    description="AI小说创作平台 - 融合inkOS与webnovel-writer精华",
    author="NovelForge",
    python_requires=">=3.11",
    packages=find_packages(),
    install_requires=[
        "openai>=1.0.0",
        "httpx>=0.25.0",
        "pyyaml>=6.0",
        "rich>=13.0.0",
        "click>=8.0.0",
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "python-docx>=1.0.0",
        "pydantic>=2.0.0",
        "jinja2>=3.1.0",
        "aiosqlite>=0.19.0",
        "sqlite-utils>=3.35",
        "markdown>=3.5",
        "python-multipart>=0.0.6",
        "websockets>=12.0",
    ],
    entry_points={
        "console_scripts": [
            "novelforge=src.cli.main:main",
        ],
    },
)
