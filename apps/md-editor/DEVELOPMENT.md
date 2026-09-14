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
