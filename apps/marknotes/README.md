# MarkNotes

Markdownを使ったスクラップブックアプリの開発用ベースです。現時点では既存Markdownエディタから取り込んだ編集画面が起動します。ノートの管理やスクラップブック固有の機能は未実装で、仕様もこの整理作業では追加していません。

ソース、テスト、依存関係、仮想環境、PyInstaller設定をこのディレクトリ内に持つ独立したuvプロジェクトです。既存エディタのソースや環境を参照せず、MarkNotes側だけで修正・検証・ビルドできます。バージョンと配布時期も個別に管理します。

## 環境作成と起動

Python 3.13以上とuvを使用します。以下は、このREADMEがある `apps/marknotes` を作業ディレクトリとして実行します。

```powershell
uv sync --locked
uv run --locked marknotes
```

このアプリ専用の `.venv` が作られます。リポジトリ直下や既存エディタの環境を有効化せずに実行してください。モジュールとして起動する場合は `uv run --locked python -m marknotes` を使います。

```powershell
uv run --locked marknotes "C:\Documents\メモ.md"
uv run --locked marknotes --sample
```

取り込んだ機能にはMarkdown編集・保存、プレビューとスクロール同期、画像・表の操作、数式・Mermaid描画、HTML/PDF出力が含まれます。既存の機能やテストを開発の出発点として引き継いでいますが、十分な実機テストが済んだ完成版ではありません。既存エディタの修正は自動では反映されません。

## 検証

```powershell
uv run --locked ruff check .
uv run --locked pytest
```

Qt/WebEngineを使うテストはGUIを利用できるWindows環境で実行します。

```powershell
New-Item -ItemType Directory -Force artifacts
uv run --locked marknotes --sample --smoke-report artifacts/smoke.json
```

`artifacts/` は実行結果の保存先で、配布対象には含めません。

## Windows向けビルド

MarkNotesのディレクトリ内で、次を単独で実行します。

```powershell
uv run --locked pyinstaller --noconfirm packaging/marknotes.spec
```

出力は `dist/marknotes/marknotes.exe`、中間ファイルは `build/` です。既存エディタのビルドを必要としません。この整理段階ではPyInstallerの実ビルドはまだ行っていません。

## 設定と取り込み元

Qtの組織名・アプリ名はともに `MarkNotes`、Windows AppUserModelIDは `Yotto.MarkNotes` です。設定、アプリデータ、WebEngineのアプリ固有領域は既存エディタから分離されます。Windowsでの通常の設定保存先は `HKEY_CURRENT_USER\Software\MarkNotes\MarkNotes`、画像改名の復旧情報はQtが解決するMarkNotesのアプリデータディレクトリ内の `image-recovery/` です。一時文書・出力の接頭辞にも `marknotes-` を使います。

アプリアイコンは、開発開始用として既存Markdownエディタのものをコピーしています。MarkNotes専用のアイコンは未作成です。`tools/build_app_icon.py` はこのアプリ内のPNGからICOを生成します。

取り込み範囲、コピー元コミット、独立管理のルールは [docs/UPSTREAM.md](docs/UPSTREAM.md) に記録しています。KaTeX/Mermaidなど同梱資産のバージョンとライセンスは `src/marknotes/resources/vendor/` に保持しています。
