# NovelForge — AI長編小説制作ワークスペース

[![Verification](https://github.com/2705911421/novelforge/actions/workflows/verification.yml/badge.svg?branch=main)](https://github.com/2705911421/novelforge/actions/workflows/verification.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)

**ドキュメント:** [简体中文](README.md) · [English](README.en.md) · 日本語

NovelForge は、長編小説のためのローカル優先 AI 制作ワークスペースです。
世界観、25 ステップの Story Bible、長編プロット、章の執筆、検索、
レビュー、対象を絞った改稿、連続執筆、バックアップ、エクスポートを、
一時停止・再開・監査できるワークフローとしてつなぎます。

inkOS と webnovel-writer の考え方を取り入れていますが、Python、FastAPI、
SQLite による独立したアプリケーションとして動作します。作品データ、
タスク状態、レビューの根拠、実行記録はローカルに永続化され、ブラウザーの
画面だけが状態を保持する構成にはなっていません。

> このプロジェクトは開発中です。機能契約、受け入れテスト、検証スクリプト、
> 実装進捗レポートを正式な基準とします。ここに書かれた機能は、自動的に
> 本番運用可能であることを意味しません。詳しくは
> [spec/features](spec/features/)、[tests](tests/)、
> [scripts/verify_features.py](scripts/verify_features.py)、
> [docs/IMPLEMENTATION_PROGRESS.md](docs/IMPLEMENTATION_PROGRESS.md) を
> 参照してください。

## 対象ユーザー

NovelForge は、長編執筆を長期的で復旧可能なプロジェクトとして扱いたい
作者やチーム向けです。

- 複雑な世界観、人物関係、時系列、伏線を管理したい作者；
- 明確な計画、コンテキスト、品質ゲートの範囲内で AI を使いたい作者；
- タスク状態、レビュー根拠、改稿理由を永続的に確認したいチーム；
- 参考資料、下書き、章、レポートを一元管理して出力したい制作者。

## 主な機能

### 企画と創作

- 下書き、確認、公開、SHA-256 スナップショットを備えた 25 ステップの
  Story Bible；
- 巻、ストーリーアーク、章目標、プロットキャンバス、タイムライン、
  世界、人物関係の管理；
- アイデア起点、企画起点、下書きインポートなど複数の創作入口と作者確認。

### AI 執筆と品質管理

- PRECHECK → 企画 → コンテキスト構成 → メモリ検索 → 下書き生成 →
  レビュー → 品質ゲート → 改稿 → Story Commit の永続化パイプライン；
- プロット、人物、世界観、テンポ、文体、伏線、AI 痕跡などを含む
  多次元の品質ゲート；
- 記録された問題または作者の指示だけを対象にする改稿。衝突や改稿上限到達時は
  needs_author_decision に入り、成功を偽装しません。

### 永続タスクと連続執筆

- lease、厳格な状態遷移、再生可能な SSE イベント、checkpoint、分類済み
  エラーを備えた SQLite タスクキュー；
- 親タスクと章ごとの子タスクによる連続執筆。各章が品質ゲートを通過してから
  次の章に進みます；
- 設定した間隔で実行する章横断レビュー。

### メモリ、RAG、StoryFlow

- Working、Episodic、Semantic、Operational の多層メモリ；
- TXT、Markdown、DOCX の解析、分割、フィンガープリント重複排除、出典追跡；
- SQLite BM25 による再現可能な検索と、任意の embedding / rerank provider；
- Story、Character、Timeline、World、Foreshadow、Context を同じ Story Graph
  で扱う StoryFlow。Canon の事実は SQLite が保持し、企画ノードと候補分岐は
  別の planning overlay に保存します。

### Studio と出力

- タスク状態と SSE 進捗を表示する FastAPI Studio；
- Provider、Model、Agent、Prompt Registry の設定；
- Markdown、TXT、DOCX、Story Bible、レビュー報告書、JSON/ink の出力；
- ローカル作品データのバックアップと復旧境界。

## クイックスタート

### 必要条件

- Python 3.11 以上；
- Python 標準ライブラリに含まれる SQLite；
- 実際に AI 生成を行う場合は OpenAI 互換モデルサービス。

### 1. 環境を作成して依存関係をインストール

Windows PowerShell:

~~~powershell
git clone https://github.com/2705911421/novelforge.git
cd novelforge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
~~~

macOS / Linux:

~~~bash
git clone https://github.com/2705911421/novelforge.git
cd novelforge
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
~~~

インポートのスモークチェック:

~~~bash
python verify.py
~~~

### 2. モデルプロバイダーを設定

テンプレートをコピーして、ローカルの設定を記入します。

~~~bash
cp .env.example .env
~~~

最低限、次の値を設定してください。

~~~text
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
NOVELFORGE_LLM_MODEL=gpt-4o
NOVELFORGE_REVIEW_MODEL=gpt-4o
NOVELFORGE_ROOT=.
~~~

レビュー用ルートは任意です。別の互換プロバイダーを使う場合は、対応する
Base URL とモデルに変更してください。.env や実際の認証情報はコミット
しないでください。

### 3. 作品とサービスを開始

作品を作成し、表示された project ID を控えます。

~~~bash
python run.py init "最初の長編小説" --genre "SF"
python run.py list
python run.py status <project_id>
~~~

別のターミナルで永続 worker を起動します。

~~~bash
python run.py worker
~~~

worker は SQLite のタスクを取得し、イベントと checkpoint を保存しながら、
資料取り込み、企画、執筆、レビュー、改稿を実行します。1 件だけ処理して
終了する場合:

~~~bash
python run.py worker --once
~~~

さらに別のターミナルで Studio を起動します。

~~~bash
python run.py serve --host 127.0.0.1 --port 8000
~~~

[http://127.0.0.1:8000](http://127.0.0.1:8000) を開いてください。
Uvicorn を直接起動することもできます。

~~~bash
python -m uvicorn src.web.studio:app --reload --port 8000
~~~

## 基本ワークフロー

### Story Bible を作成して公開

~~~bash
python run.py wizard <project_id> --input "記憶を売買できる近未来都市。"
python run.py bible <project_id> show
python run.py bible <project_id> set <step_key> "このステップの下書き"
python run.py bible <project_id> confirm <step_key>
python run.py bible <project_id> publish
~~~

公開には 25 ステップすべての確認が必要です。厳格モードでは、公開済み
Story Bible がない限り PRECHECK が執筆を停止します。

### 参考資料を取り込み、検索

~~~bash
python run.py ingest <project_id> ./references/world.md --type world
python run.py ingest <project_id> ./references/character.docx --type character
python run.py ingest <project_id> ./references/style.txt --type style
python run.py rag-search <project_id> "記憶売買のルール" --top-k 5
~~~

取り込みは worker にキューされ、重い解析とインデックス作成は HTTP
リクエスト内では実行されません。

### 執筆、レビュー、改稿

Studio で巻、アーク、章目標を設定してから、章をキューに入れます。

~~~bash
python run.py write <project_id> 1 --context "主人公は改ざんされた取引記録を初めて発見する。"
python run.py write <project_id>
python run.py status <project_id>
~~~

パイプラインは事前チェック、コンテキスト構成、メモリ検索、下書き生成、
レビュー、品質ゲート、改稿、Story Commit を実行します。ゲート失敗や
未解決の衝突は作者の判断境界として残ります。

### 連続執筆と出力

~~~bash
python run.py continuous <project_id> --start 1 --count 5 --context "抑制された緊張感のある文体を保つ。"
python run.py export <project_id> --format md
python run.py export <project_id> --format txt --output ./exports/novel.txt
python run.py export <project_id> --format docx --approved-only
~~~

連続執筆は 5〜200 章に対応し、開始前に作者の確認を求めます。

## CLI 主要コマンド

| コマンド | 用途 |
| --- | --- |
| python run.py init | 作品を作成し、必要なら世界観構築をキュー |
| python run.py wizard | Story Bible / 世界観構築リクエストをキュー |
| python run.py bible | Story Bible の表示、編集、確認、公開 |
| python run.py ingest | 参考資料を保存し、解析と索引作成をキュー |
| python run.py rag-search | 索引済み文書チャンクを検索 |
| python run.py write | 1 章の執筆をキュー |
| python run.py continuous | 5〜200 章の連続執筆をキュー |
| python run.py export | 承認済み本文とレポートを出力 |
| python run.py status | 作品と章の状態を表示 |
| python run.py list | ローカル作品を一覧表示 |
| python run.py mindmap | マインドマップ HTML を生成 |
| python run.py timeline | タイムライン HTML を生成 |
| python run.py serve | Studio Web を起動 |
| python run.py worker | SQLite 永続タスク worker を実行 |

完全なオプションは python run.py --help または各コマンドの --help を
実行してください。

## アーキテクチャとデータ境界

SQLite がローカルの権威ある事実ストアです。API はタスクを作成・読み取りし、
永続化されたイベントをストリームします。永続 worker がタスクを実行し、
ファイルシステムには添付、出力、バックアップを保存します。StoryFlow は
再構築可能なグラフ投影を読み取り、第二の事実ストアを作りません。

~~~mermaid
flowchart TD
    AUTHOR[作者] --> ENTRY[CLI または Studio]
    ENTRY --> API[FastAPI API と SSE]
    API --> RUNTIME[Task runtime]
    RUNTIME --> DB[(SQLite)]
    WORKER[永続 worker] --> RUNTIME
    WORKER --> PIPELINE[執筆とドメインパイプライン]
    PIPELINE --> GATEWAY[Model gateway と agent router]
    GATEWAY --> PROVIDER[OpenAI 互換 provider]
    PIPELINE --> MEMORY[Memory と RAG]
    MEMORY --> DB
    DB --> READMODEL[Studio と StoryFlow read model]
    READMODEL --> AUTHOR
~~~

次のローカル実行データはコミットしないでください。

| パス | 内容 | ルール |
| --- | --- | --- |
| projects/ | 作品、SQLite データベース、添付 | ローカル保存、意図的にバックアップ |
| .env / .novelforge-secrets/ | 設定と認証情報の参照 | コミット禁止 |
| .novelforge-backups/ | データベースと添付のバックアップ | バックアップ機能で管理 |
| exports/ | 小説とレポートの出力 | 納品用に別途保存 |
| studio/ / test-output/ | ローカルセッションと診断結果 | バージョン管理に追加しない |

セキュリティ問題の報告と秘密情報の扱いは [SECURITY.md](SECURITY.md) を
参照してください。

## テストと検証

変更を提出する前に、関連するチェックを実行します。

~~~bash
python -m pytest -q --tb=short
ruff check src tests
pyright src tests
python verify.py
python scripts/verify_features.py
python scripts/generate_progress.py --verify
python scripts/check_protected_files.py
~~~

GitHub Actions の Verification workflow には次の job があります。

1. protected-artifacts: 保護ファイルの検査；
2. acceptance: 機能契約の受け入れと進捗検証；
3. quality: Ruff、Pyright、インポートのスモークチェック。

ローカル依存関係、provider の認証情報、外部サービスの都合で実行できない
チェックがある場合は、Pull Request に理由を記録してください。結果を
成功に見せるために検証を弱めたり、スキップしたりしないでください。

## 開発とドキュメント

- [CONTRIBUTING.md](CONTRIBUTING.md): コントリビューション規則；
- [CLAUDE.md](CLAUDE.md): 工学的制約と保護された検証資産；
- [DESIGN.md](DESIGN.md): 設計概要；
- [docs/](docs/): アーキテクチャ、フェーズ、監査、StoryFlow の証拠；
- [spec/features/](spec/features/): 機能契約と受け入れ境界。

Story System、Writing Pipeline、Review Gate、Revision、Continuous Writing、
Memory/RAG、Backup/Restore を変更する場合は、成功、失敗、永続化、復旧の
経路をカバーしてください。ローカル作品データ、データベース、バックアップ、
ログ、ブラウザー生成物、認証情報はコミットしないでください。

## ライセンスとサポート

NovelForge は [MIT License](LICENSE) で公開されています。

- バグと機能要望: [GitHub Issues](https://github.com/2705911421/novelforge/issues)
- 質問とコミュニティ: [GitHub Discussions](https://github.com/2705911421/novelforge/discussions)
- 完全なプロジェクト文書: [docs/](docs/)
