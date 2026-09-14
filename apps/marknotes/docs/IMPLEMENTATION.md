# MarkNotes 初期実装と検証

実装日: 2026-09-13

## 実装した機能

- タイトルバー内の複数タブ、新規ノート、並べ替え、最終表示が古いタブの「…」への収納。
- ノート単位のUndo/Redoと、表示方法・カーソル・スクロール位置の保存。
- SQLiteを正本にした自動保存。本文・更新日時・検索索引を同じトランザクションで更新し、古いrevisionによる上書きを防止。
- タブ閉鎖とノート保持の分離、一括閉鎖、Ctrl+Shift+Tによる直近100操作の復元。再起動後も利用可能。
- 履歴の作成日・更新日分類、ノートのピン、重ね表示と固定表示のサイドバー。
- 全文検索のノート別結果、一致抜粋、追加の抜粋、本文・プレビューのハイライトと前後移動。日本語1〜2文字にも対応。
- ノート別assetsへの画像・一般ファイル貼り付け、一般添付リンクの起動、Markdownと添付の取り込み・書き出し。
- 画像の外部編集検出と所有ノートの更新日時反映、改名時の原本保持とUndo対応。
- ライブラリ全体のバックアップ・別ライブラリへの復元、ライブラリ切り替え、検索索引の再構築。
- ライブラリ単位の起動ロックと、二重起動時のウィンドウ・ファイル引き継ぎ。
- MarkNotes専用の設定画面とPyInstallerビルド。

既存の編集・プレビュー・表・数式・Mermaid・HTML/PDF出力の部品を再利用している。既存Markdownエディタのプロジェクトには変更を加えていない。

## 主な実装場所

| ファイル | 責務 |
| --- | --- |
| `src/marknotes/notebook.py` | ノートウィンドウ、非同期保存と画面の接続、閉鎖・復元・添付・検索の操作 |
| `src/marknotes/notebook_runtime.py` | ノート別QTextDocument、編集部品向けのアダプター、バックグラウンド処理 |
| `src/marknotes/notebook_store.py` | SQLite、ノート・タブ・履歴・FTS5・バックアップ |
| `src/marknotes/notebook_widgets.py` | タブ、オーバーフロー、サイドバーと検索結果 |
| `src/marknotes/managed_assets.py` | 添付の段階的コピーと公開、管理パス検査、取り込み・書き出し |
| `src/marknotes/notebook_highlight.py` | Unicodeの対応範囲、安全な抜粋、DOMを変更しないプレビュー強調 |
| `src/marknotes/notebook_instance.py` | QLockFile / QLocalServerによる二重起動制御 |
| `src/marknotes/notebook_settings.py` | ノートアプリの設定 |

編集用文書は開いたノートごとに保持し、エディタの表示先を切り替える。QWebEngineViewはウィンドウ全体で1個を使用する。起動時はタブの管理情報を読み、本文は選択時に読み込む。

添付は完成後にリンクを挿入し、途中で別のタブへ切り替えても元のノートのカーソルへ挿入する。取り込み中の閉鎖・終了は処理完了後の最新本文を保存してから確定する。待機中の閉鎖連打や新規作成完了との競合も検証対象にした。

## 検証結果

全904件のテストが82.99秒で成功した。Ruffの静的チェックとフォーマット確認も成功している。検証コマンドと結果は下記のログへ記録している。配布版はDLL探索範囲を修正したクリーンビルドで、実EXEから画像表示・136行の描画・先頭/中間/末尾のスクロール同期が成功した。

- 全体テスト: `artifacts/notebook-implementation/pytest-release.log` / `pytest-release.xml`
- 静的チェック: `ruff check` と `ruff format --check`
- 配布ビルド: `artifacts/notebook-implementation/build-release.log`
- ソース起動: `artifacts/notebook-implementation/source-smoke.json`
- 配布版起動: `artifacts/notebook-implementation/frozen-release-smoke.json`
- 画面確認: `marknotes-search.png` / `marknotes-fixed.png`
- 100タブ復元: `artifacts/notebook-implementation/100-tabs.json`

100タブを復元する確認では、本文を読み込んだノートが1件、QWebEngineViewが1個、92タブが「…」内となり、選択中のタブが表示されることを確認した。

検索の測定は10,000ノート・本文約48.45MiB、最初の50件の原文位置と抜粋生成を含む。日本語3文字は約97ms、2文字約114ms、1文字約116ms、共通英語約208msだった。現環境の測定値であり、すべてのPCやデータ分布に対する性能保証ではない。再現スクリプト・環境・複数回の結果は `search_benchmark.py` / `search-benchmark.json` に記録した。

## 検証中に修正した問題

- 非表示のプレビューを開く時、表示幅が確定する前の座標でスクロールを復元すると、直後のリサイズによるブラウザの補正をユーザースクロールと誤認する競合を確認した。レイアウト更新中のスクロール通知を除外する修正と、リサイズでスクロール上限が縮む回帰テストをMarkNotes側へ追加した。
- 最初のEXEでは、ビルド環境のPATHにある別ツールのICU DLLが収集され、QtCoreの読み込みに失敗した。Qtが要求するシンボルとそのICUのABIが異なっていた。MarkNotesのspecでDLL探索先をPython・Qt・Windowsへ限定し、無関係なツールのDLLが混入しないようにした。

## 今後の実利用で確認する範囲

長時間利用、複数モニター・異なる実DPI、実際の日本語IMEや外部画像編集アプリとの組合せは継続して確認する。初期実装の時点で、タブを閉じた後のUndo履歴復元、ノートの完全削除・ごみ箱、クラウド同期、添付内部の全文検索、OCRは対象に含めていない。

ノートや添付を破壊しないことを優先し、未参照添付の自動削除は行わない。バックアップは配布前後・移行前の確認に利用できる。
