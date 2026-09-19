# Markdown Editor 開発ガイド

アプリの紹介、インストール、起動、ビルド、操作方法は [README.md](README.md) を参照してください。この文書には開発・検証と実装に関する情報をまとめています。

## 開発環境と進捗

PySide6 / QWebEngineViewを使うWindows向けMarkdownエディタです。段階1〜6（[開発計画](PLAN.md)のフェーズ0〜5）を実装しています。仕様は概ね固まっていますが、実際のテストと不具合修正を引き続き行います。段階7の配布ビルド・配布環境での検証は未実施です。

このアプリのディレクトリ `apps/md-editor/` で、Python 3.13以上とuvを使います。Python環境と依存関係はuvで管理します。MarkNotesの開発状況とは独立して、必要な時点で修正・テスト・PyInstallerビルドを行えます。MarkNotesの環境は不要です。

```powershell
uv sync --locked
```

## 検証

```powershell
uv run --locked pytest -q
uv run --locked ruff check src tests packaging tools
uv run --locked ruff format --check src tests packaging tools
```

検証範囲と結果は [VERIFICATION.md](VERIFICATION.md) に記録しています。

起動オプション `--sample` で検証サンプルを開き、スモークレポートを書き出せます。

```powershell
uv run --locked md-editor --sample --smoke-report artifacts/stage34-smoke.json
```

### スクロール同期の確認

1. 左右それぞれのホイール・スクロールバーで、同じ見出しや段落へ追従することを確認します。
2. 表の各行、長いコード、縦長画像の前後を確認します。
3. 「最終行を上端へ」で、最後の行を最上部に移動できることを確認します。
4. ペイン幅・折り返し・テーマを変更した後や、本文の編集後も再確認します。

空行や区切り記号、画像・段落の内部は近くの内容を基準に位置を補間します。左右の全ての文字を同じ高さに並べる仕様ではありません。スクロール用の末尾余白は保存内容に追加しません。

## ビルドと成果物

ビルドとEXEの実行方法は [README.mdのビルド（Windows EXE）](README.md#ビルドwindows-exe) を参照してください。出力先はこのアプリ内の `dist/md-editor/md-editor.exe` です。

移動前の段階1〜2の旧配布物は `artifacts/legacy-dist/` に保管しています。現在の機能は `uv run --locked md-editor` で確認できます。

## アイコン

アプリアイコンはMarkdownの「M」と左右2ペインを組み合わせた、外周が角丸の透過デザインです。ウィンドウとWindowsタスクバーに設定し、配布用EXEのビルド設定にも同じアイコンを指定しています。

アイコン画像は `src/md_editor/resources/app-icon.png`、Windows用の複数サイズを収録したICOは同じディレクトリの `app-icon.ico` です。ICOだけを作り直す場合は次を実行します。

```powershell
uv run --locked python tools/build_app_icon.py
```

生成指示とデザインの記録は [アイコンの制作記録](src/md_editor/resources/app-icon-design.md) を参照してください。

## 実装の補足

- 従来の独立したツールバー行は廃止し、スクロール同期・折り返し・先頭／最終行への移動は表示メニューへ集約しています。書式操作は右クリックメニューに設けています。
- 空のリスト項目からの退出と2段階Backspaceは、参考デモのインデント仕様に合わせています。
- HTMLからMarkdownへの変換にはmarkdownifyを使います。Markdown／コードと判定できるプレーンテキストや、HTMLのコードラッパーではプレーンテキストを優先します。
- KaTeX 0.18.7／Mermaid 11.17.2と数式用フォントをローカル同梱しています。同梱資産の説明とライセンスは [vendor/README.md](src/md_editor/resources/vendor/README.md) を参照してください。
- 外部アプリによる画像変更は、画像ファイルやフォルダの置換保存を監視してプレビューへ反映します。
- HTML出力では、ローカル画像のほか、HTMLの `picture` / `srcset`、CSSの `url()` / `@import` / フォントも処理します。
- 出力する数式は描画済みHTML/MathMLとフォント、Mermaidは描画済みSVGを含み、JavaScriptやCDNへの依存を残しません。PDFも同じHTMLを使い、画像・フォントの読み込み完了後に生成します。通常プレビューの末尾スクロール用余白やソース位置属性は出力しません。

## 計画と開発記録

- [開発計画](PLAN.md)
- [開発セッションの記録](SESSION_SUMMARY.md)
- [検証記録](VERIFICATION.md)
- [配置方針](../../docs/project-structure.md)
- [参考デモ](../../references/prompt_editor_demo/README.md)

## タイムスタンプ・リンクの編集

- 「挿入」→「タイムスタンプ」で、PCのローカル日時を `YYYY-MM-DD HH:mm:ss` 形式でカーソル位置へ挿入します。選択中の文字があれば置き換え、1回の「元に戻す」で復元できます。
- 「編集」→「形式を指定して貼り付け」→「リンクとして貼り付け」で、クリップボードの単一URLをMarkdownリンクに変換します。選択文字を表示名に使い、未選択ならURLを表示します。URL内の括弧や表示名のMarkdown記号をエスケープし、既存のパーセントエンコードを保持します。
- 同じ操作はソースの右クリックメニューからも使えます。リンク貼り付けは有効な単一URLがある時だけ有効になり、複数URL・通常の文章・相対パス・実行系スキームは対象外です。
- Markdown／HTMLリンクとHTML出力は、`obsidian:` や `vscode:` などの独自スキームも保持します。プレビューのクリック、または右クリックの「リンクを開く」でOSの既定ハンドラへ渡します。HTTP／HTTPSは既定ブラウザ、独自スキームは登録されたアプリで開きます。
- `link_schemes.py` でリンクのスキーム判定を共有します。`javascript:`／`vbscript:`／`data:`、ブラウザ内部スキームは無効にし、自動遷移から外部アプリを起動しません。ローカルファイルは従来のアプリ別ルールに従います。
- `test_link_timestamp_actions.py` で日時・URL変換・選択文字・Undo／Redo・メニュー状態を、リンクとプレビューのテストで独自スキームの描画・クリックと危険なスキームの拒否を検証します。

### 2026-09-16 検証

md-editorは全809件に加え、最終追加のHTML出力・外部リンク関連76件が成功しました。 両アプリの `ruff check` と `ruff format --check` も成功しています。

Qt検証は `QT_QPA_PLATFORM=offscreen`、`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` で実行しました。既存環境を `uv run --locked --no-sync` で使用し、ビルドは実行していません。

## プレビューからのチェック操作

- Markdownのタスクリスト（`- [ ]` / `- [x]`）は右ペインで操作できます。有効なチェックボックス本体の上だけ指先カーソルを表示し、項目の文字や行の余白をクリックしてもチェックは変わりません。
- チェック操作は左ペインの状態文字1文字だけを変更します。選択範囲・カーソル・表示位置を保ち、1回のUndo／Redoで戻す／やり直すことができます。プレビューのみの表示でも操作できます。
- `task_lists.py` が解析済みのリスト項目からチェック位置を生成し、`RenderedDocument.task_markers` と本文のリビジョンで更新先を確認します。古いプレビュー、読み取り専用、コード内やHTMLで偽装された位置からの更新要求は受け付けません。
- HTML／PDF出力はチェック状態を保存し、プレビュー編集用の行・列属性を取り除いた静的なチェックボックスを使います。
- `test_task_lists.py`、`test_task_preview.py`、`test_preview_task_editing.py` で入れ子・引用・番号付きリスト、クリック範囲、ライト／ダーク表示、本文連動、Undo／Redo、古いリビジョンの拒否を検証します。

チェック操作追加後の全回帰テストは852件成功し、`ruff check` と `ruff format --check` も成功しました。 並列実行で失敗した既存の表示切り替え時スクロールテストも、単独の全体再実行で成功しました。 非表示・GPU無効の既存環境で実行し、ビルドは行っていません。

## 検索ハイライトと選択範囲の左右同期

- `Ctrl+F` の検索語を左のMarkdown本文と右の表示テキストでハイライトします。大小文字・正規表現は同じPythonの検索条件を使用し、表示は左右とも最大1,000一致です。件数・次／前・置換の対象はMarkdown本文です。表示されない記号やリンク先URLは左だけが対象になります。
- `SearchHighlights` が描画後の表示テキストを取得し、検索範囲をCSS Highlightへ渡します。検索バーを閉じる、空の検索語、無効な正規表現ではハイライトを解除します。編集・置換・Undo／Redo・ファイル変更・テーマ変更・再描画に追従し、世代と文書リビジョンで古い通知を拒否します。
- 次／前への移動は検索結果と左右のハイライトを再利用します。現在の一致箇所は通常の選択同期で右にも表示し、移動ごとに本文DOMやハイライトを作り直しません。
- 左右どちらで選択しても、対応する範囲をもう片方でも選択します。逆向きの選択と操作側のフォーカスを保持します。左の選択がMarkdown記号の途中で始まる場合は、選択範囲内の次の表示文字から右を選択し、終了が記号の途中なら直前の表示文字までにします。記号だけの選択では相手側を解除します。
- 右から左では最初と最後の表示文字に対応する連続したソース範囲を選択し、間の構文記号も含めます。数式・図・画像は要素単位で対応します。コピーは最後に操作したペインの選択を使い、本文・Undo履歴・保存状態は変更しません。
- `selection_mapping.py` が解析中の元文字位置を保持し、`selection-sync.js` がUTF-16位置とDOM Rangeを対応させます。`selection_sync.py` はQt側の選択と連動し、描画リビジョン・選択の世代・操作側を確認して同期の往復と遅延要求による上書きを防ぎます。
- md-editorのプレビューでは `MappedText` を参照URLの正規化にも渡します。`rewrite_destinations` が変更後URLを元のURL範囲に対応させ、`render_markdown(..., original_source=source)` が元本文基準の位置を生成します。保存先変更後や画像復元後のUndoで表示URLの長さが変わっても、その後の文字位置を維持します。画像キャッシュのURL書き換えはHTML生成後に行います。
- 選択・検索の操作で本文DOMを置き換えず、HTML／PDF出力からは選択同期の内部属性を除去します。`test_search_highlight*.py`、`test_selection_mapping.py`、`test_preview_selection_sync.py`、`test_selection_sync.py` が検索条件、ネイティブ選択、非同期競合、保存・Undoとの整合性を検証します。
- コードブロックの検索テキストは生成された行要素の境界から改行を補い、空行用のBRを二重に数えません。別行をつなげた誤一致を防ぎ、複数行の正規表現も表示どおりの改行数で判定します。

2026-09-17の最終検証では `pytest -q` の全923件、`ruff check`、`ruff format --check` が成功しました。`QT_QPA_PLATFORM=offscreen`、`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` と既存環境の `uv run --locked --no-sync` を使用しました。ビルドは実行していません。

## ソースのフォントとプレビュー倍率

- 「表示」メニューからソースのフォント・文字サイズ（6〜72pt）、プレビュー倍率（25〜500%）を変更できます。ソースの標準はCascadia Mono・11pt、倍率の標準は100%です。
- `display_preferences.py` がメニュー・入力・アプリ別設定を管理します。`display/sourceFont` にQFontの文字列表現、`display/previewZoom` に倍率を保存し、起動時に読み込みます。キャンセル時は変更せず、不正な保存値は標準へ戻します。
- ソースは `SourceEditor.set_source_font` で上端の論理行位置を保存し、フォント、タブ幅、行番号欄を更新して折り返しを再計算し、同じ内容位置へ戻して同期を通知します。文字位置・選択・Undo履歴は維持します。折り返し途中の位置はQtの表示行単位に丸めます。
- プレビューは `PreviewPane.set_zoom_factor` でWebEngineの表示倍率を変更し、Markdown行と描画位置の対応を再計測・復元します。倍率変更と文書更新・表示切替が重なっても古い非同期通知で位置を上書きしないようにします。 倍率変更と表示復元には独立した世代番号を使い、倍率変更直前に未通知のユーザースクロールがあれば先に同期します。
- `test_editor.py`、`test_preview_zoom.py`、`test_display_preferences.py` でフォント・倍率変更、折り返し途中、先頭・末尾、双方向同期、同期OFF、単独ペイン、設定の保存・復元、本文と選択・Undoの保持を検証します。

### 2026-09-19 検証

`uv run --locked --no-sync pytest -q` は全971件成功・終了コード0。`ruff check src tests packaging tools` と `ruff format --check src tests packaging tools` も成功しました。Qtは非表示モード、WebEngineはGPU無効で実行しています。ログは `artifacts/display-preferences-pytest.txt` です。個別のWebEngine試験では判定完了後のネイティブアクセス違反が発生したため確認範囲を広げ、最終の全体実行では正常終了まで確認しています。EXEの再ビルドは行っていません。
