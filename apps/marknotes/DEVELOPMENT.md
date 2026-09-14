# MarkNotes 開発ガイド

アプリの紹介、インストール、起動、ビルド、操作方法は [README.md](README.md) を参照してください。この文書には開発・検証と内部構成に関する情報をまとめています。

## 開発環境

MarkNotesは既存Markdownエディタとは別のuvプロジェクトです。このディレクトリ内のソース、テスト、依存関係、仮想環境、PyInstaller設定を使って、単独で修正・検証・ビルドできます。

Python 3.13以上とuvを用意し、この文書がある `apps/marknotes` ディレクトリで実行します。

```powershell
uv sync --locked
```

作業時は [AGENTS.md](AGENTS.md) の規則に従ってください。

## 自動検証

```powershell
uv run --locked pytest -q
uv run --locked ruff check src tests packaging tools
uv run --locked ruff format --check src tests packaging tools
```

自動テストは既定でQtの非表示モード（`QT_QPA_PLATFORM=offscreen`）を使い、デスクトップへウィンドウやメニューを表示しません。メニュー・確認ダイアログは表示せず、構築内容とアクションを検査します。実画面での確認は明示的に依頼された場合だけ行います。

検証用ライブラリは一時ディレクトリまたは `artifacts/` に作り、ユーザーのライブラリを削除テストに使わないでください。

## 手動検証

実画面での確認を明示的に依頼された場合は、次のコマンドで起動・画像表示・スクロール同期を別ライブラリで確認できます。

```powershell
uv run --locked marknotes --library artifacts/smoke-library --sample --smoke-report artifacts/smoke.json
```

`--library` は検証用・一時利用の保存先指定にも使えます。検証結果は `artifacts/` に保存します。

## ビルドとアイコンの更新

ビルド手順は [README.mdのWindows向けビルド](README.md#windows向けビルド) を参照してください。ビルド出力はこのプロジェクトの `dist/`、中間ファイルは `build/` に入ります。

ビルド後、対象EXEのShellアイコン識別子を取得して更新を通知するには、次を実行します。

```powershell
uv run --locked python tools/refresh_app_icon.py
```

Explorerの小アイコンだけ古い表示が残る場合は、次のコマンドでShell全体のアイコン・サムネイルキャッシュの再取得を要求できます。ファイル関連付けの変更、キャッシュファイルの削除、Explorerの再起動は行いません。

```powershell
uv run --locked python tools/refresh_app_icon.py --refresh-shell-cache
```

アイコンは既存エディタのMと左右カードのモチーフを引き継ぎ、プラム色・クリーム色・琥珀色と重ねたノートで区別したMarkNotes専用デザインです。[アイコンの制作記録](src/marknotes/resources/marknotes-icon-design.md) に生成指示と更新手順を記載しています。

## アプリ識別子と保存構成

Qtの組織名・アプリ名は `MarkNotes`、Windows AppUserModelIDは `Yotto.MarkNotes` です。

ライブラリの既定の保存先は、Qtが返す `AppLocalDataLocation` 配下の `library/` です。このWindows環境では `%LOCALAPPDATA%/MarkNotes/MarkNotes/library/` です。

```text
library/
├── library.sqlite3
└── notes/<note_id>/assets/
```

本文・日時・ピン・表示状態・タブ状態・検索索引はSQLite、添付はノート別の `assets/` に保存します。通常のノート削除は、DBファイル内部の過去データまで消去する機能ではありません。削除の動作と注意事項は [README.md](README.md#基本操作)、実装方針は [通常のノート削除](docs/NOTE_DELETION.md) を参照してください。

## 設計と開発記録

- [開発計画](PLAN.md)
- [実装と検証の記録](docs/IMPLEMENTATION.md)
- [UI調整](docs/UI_REFINEMENT.md)
- [通常のノート削除](docs/NOTE_DELETION.md)
- [取り込み元と独立管理方針](docs/UPSTREAM.md)
