# プロジェクト整理の実施記録

実施日: 2026-09-12

## 実施内容

既存Markdownエディタを `apps/md-editor/` へ移し、`apps/marknotes/` に独立したMarkNotesの開発用ベースを作成した。両アプリは専用のソース、resources、テスト、pyproject、lock、仮想環境、PyInstaller設定を持つ。

MarkNotesは既存エディタの機能を独立コピーした状態で、スクラップブック固有の機能は未実装。コピー元と変更範囲は[取り込み記録](../apps/marknotes/docs/UPSTREAM.md)を参照。今後の修正・テスト・ビルド・配布はアプリごとに進める。

- 既存エディタのPythonパッケージ名・起動コマンド・設定識別子は維持した。
- MarkNotesのパッケージ・起動コマンドを `marknotes`、Qtの組織名とアプリ名を `MarkNotes`、Windows AppIDを `Yotto.MarkNotes` とした。
- MarkNotesの一時ファイル等の識別子も分離し、両アプリの相互importやローカルパス依存は作っていない。
- `参考/prompt_editor_demo/` を `references/prompt_editor_demo/` へ移動した。
- 既存の検証成果物をエディタ内の `artifacts/`、旧配布物を `artifacts/legacy-dist/` に保管した。旧EXEは段階1〜2のもので、現在版の配布物ではない。
- 元の `.venv/` を移動せず、両アプリのlockから個別に環境を再作成した。旧ルートの仮想環境・テストキャッシュ・ビルド中間物は削除した。
- ルートの `.uv-cache/` はダウンロード・ビルドキャッシュとして再利用した。共通の依存定義や仮想環境としては使用していない。
- ルートREADMEに両アプリの独立した操作手順を用意し、既存文書の移動に伴うリンクと起動案内を更新した。

## 保存した基準

整理前はコミットがなかったため、次の初期コミットに元のソースと資料を保存した。

- コミット: `68db79106433c67526fa3129f9814616459283df`
- メッセージ: `Preserve Markdown editor baseline before project separation`
- 整理作業ブランチ: `codex/separate-marknotes`

Git管理外の旧EXEと検証成果物は上述の保存先に残している。Gitだけではこれらの生成物を復元できないため、保存先とハッシュ記録も保全対象となる。

## 内容の保全確認

- 既存の `src/`、`tests/`、`packaging/`、`tools/`、`examples/` に属する213ファイルは、移動前後でSHA-256が一致した。エディタの機能コードは変更していない。
- 既存の検証成果物と旧配布物、計3,082ファイルは移動直後のSHA-256照合で一致した。以後のテストで通常の確認画像が再生成されることはある。
- 既存エディタの `uv.lock` は内容を維持した。MarkNotesのlockはプロジェクト名を変更し、第三者依存パッケージの固定バージョンは一致している。
- 両アプリのvendor資産129ファイルは内容が一致し、ライセンス文書も引き継いだ。

移動前のファイル一覧とハッシュはエディタの `artifacts/project-reorganization/source-before.json` と `preserved-before.json` に記録した。

## 検証結果

Windows / Python 3.13.3 / uv 0.11.9を使用。GUIテストは順番に実行した。

| 対象 | 結果 |
| --- | --- |
| 既存エディタ・移動前の全テスト | 736成功、67.69秒 |
| 既存エディタ・移動後の全テスト | 736成功、67.92秒 |
| MarkNotes・専用環境での全テスト | 736成功、69.70秒 |
| Ruff check | 移動前のエディタと整理後の両アプリで成功 |
| Ruff format --check | 各78ファイル、変更不要 |
| 専用環境の分離 | 両方で自分のパッケージを新配置からimportでき、他方のパッケージは見つからないことを確認 |
| PyInstaller設定の参照先確認 | 入口・探索パス・resources・アイコンが各アプリ内に存在し、EXE名も分かれていることを確認 |
| 既存エディタの起動・プレビュー確認 | 専用uvコマンドから起動。136行のサンプル、画像読み込み、先頭・中央・末尾のスクロール確認成功 |
| MarkNotesの起動・プレビュー確認 | 専用uvコマンドから起動。同じサンプル・画像・3地点のスクロール確認成功 |

PyInstaller設定はビルド処理をスタブに置き換えて参照先を検査した。実際のEXE生成、Python未導入環境での配布確認、OSのファイル関連付け確認は実施していない。これらは各アプリの独立した今後の作業とする。

既知の非表示プレビュー位置復元テストの断続的失敗は今回の実行では発生しなかった。既知事項を解消したと判断するものではなく、今後の実操作テストと修正を引き続き行う。

## 確認ログ

- [移動前テスト](../apps/md-editor/artifacts/project-reorganization/before-pytest.log)
- [移動後テスト](../apps/md-editor/artifacts/project-reorganization/after-pytest.log)
- [MarkNotesテスト](../apps/marknotes/artifacts/project-reorganization/pytest.log)
- [配置・依存・ビルド参照先の確認](../apps/md-editor/artifacts/project-reorganization/layout-checks.json)
- [既存エディタの起動確認](../apps/md-editor/artifacts/project-reorganization/smoke.json)
- [MarkNotesの起動確認](../apps/marknotes/artifacts/project-reorganization/smoke.json)

これらの実行ログはGit管理外。検証結果の要約はこの文書で管理する。起動時のスクリーンショットも同じディレクトリに保存されている。
