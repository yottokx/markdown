# Markdownアプリの開発リポジトリ

既存MarkdownエディタとMarkNotesを、それぞれ独立したuvプロジェクトとして管理します。

| アプリ | 役割・現在の状態 | ソース・利用方法 |
| --- | --- | --- |
| Markdown Editor | 任意の `.md` ファイルを開くエディタ。継続してテスト・修正する | [apps/md-editor](apps/md-editor/README.md) |
| MarkNotes | タブ・自動保存・履歴・ピン・全文検索・添付管理を備えたMarkdownスクラップブック | [apps/marknotes](apps/marknotes/README.md) |

## 前提

基本的に自分用のプロジェクトです。
不特定多数の人に使ってもらうアプリとしては設計していません。
予告なく破壊的変更を行います。

## 起動と環境構築

リポジトリルートから、必要なアプリだけ環境を作成して起動できます。Python 3.13とuvを使用します。

```powershell
# 既存Markdownエディタ
uv --directory apps/md-editor sync --locked
uv --directory apps/md-editor run --locked md-editor

# MarkNotes
uv --directory apps/marknotes sync --locked
uv --directory apps/marknotes run --locked marknotes
```

アプリのディレクトリに移動して、通常の `uv run --locked md-editor` / `uv run --locked marknotes` として実行しても構いません。ファイル引数は絶対パスを指定すると作業ディレクトリによる混乱を避けられます。

## 独立したテストとビルド

```powershell
# 既存エディタだけを検証・ビルド
uv --directory apps/md-editor run --locked pytest -q
uv --directory apps/md-editor run --locked ruff check src tests packaging tools
uv --directory apps/md-editor run --locked ruff format --check src tests packaging tools
uv --directory apps/md-editor run --locked pyinstaller --noconfirm packaging/md-editor.spec

# MarkNotesだけを検証・ビルド
uv --directory apps/marknotes run --locked pytest -q
uv --directory apps/marknotes run --locked ruff check src tests packaging tools
uv --directory apps/marknotes run --locked ruff format --check src tests packaging tools
uv --directory apps/marknotes run --locked pyinstaller --noconfirm packaging/marknotes.spec
```

ビルド出力はそれぞれ `apps/md-editor/dist/md-editor/` と `apps/marknotes/dist/marknotes/` です。他方の環境構築・修正・テスト成功・ビルド完了を必要としません。ルートにuvプロジェクトや共通ロックファイルは置かず、各アプリの `pyproject.toml`・`uv.lock`・`.venv` を使用します。

バージョンと配布時期はアプリごとに決めます。共通の不具合修正を反映する場合も、各アプリで取り込み・検証します。別ブランチで同時作業する場合はGit worktreeを分けられます。

## 配置と記録

- [配置方針](docs/project-structure.md)
- [整理作業の記録・検証結果](docs/project-reorganization.md)
- [既存エディタの開発セッション記録](apps/md-editor/SESSION_SUMMARY.md)
- [既存エディタの検証記録](apps/md-editor/VERIFICATION.md)
- [MarkNotesの開発計画](apps/marknotes/PLAN.md)
- [MarkNotesの取り込み元と範囲](apps/marknotes/docs/UPSTREAM.md)
- [参考デモ](references/prompt_editor_demo/README.md)

既存エディタの過去の検証成果物は `apps/md-editor/artifacts/`、段階1〜2の旧配布物は `apps/md-editor/artifacts/legacy-dist/` に保管しています。これらはGit管理外です。今回の整理では配布EXEの新規ビルドは行っていません。

ルートに残る `.uv-cache/` は再利用可能なダウンロード・ビルドキャッシュです。両アプリの依存バージョンは個別のlockで決まり、キャッシュがなくても各アプリの環境は構築できます。
