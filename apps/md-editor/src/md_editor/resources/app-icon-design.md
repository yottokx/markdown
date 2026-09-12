# アプリアイコン

2026-09-10、内蔵の `image_gen` でこのアプリ用に新規作成。

- モチーフ：Markdownの「M」と、ソース／プレビューの左右2ペイン。濃い青を背景に、白とシアンで描画。
- `app-icon.png`：採用画像の外周を角丸にした透過PNG。角の半径は一辺の22%。中央の絵柄と色は元画像と同一。
- `app-icon.ico`：Windows用。16 / 20 / 24 / 32 / 40 / 48 / 64 / 128 / 256 pxを収録。
- ウィンドウ、タスクバー、PyInstallerのEXEアイコンとして共通使用。

ICOは `uv run python tools/build_app_icon.py` でPNGから再作成できます。サイズ変換とICOへの格納だけを行い、EXEはビルドしません。

角丸化は元の絵柄を保持する透過マスクで適用しています。以下は角丸加工前の画像の生成指示です。

## 採用画像の生成プロンプト

```text
Use case: logo-brand. Create exactly ONE finished square Windows application icon for a two-pane Markdown editor. Full-bleed design: the ENTIRE square canvas is a continuous solid deep ink-blue background (#102F51), opaque and filled to all four corners and all four edges. There is NO transparency and no rounded outer tile, no checkerboard, no outer margin or frame, no white border. Center a bold simple two-pane mark within the blue square. The left pane is a white rounded rectangular outline containing a large thick geometric white uppercase M. The right pane is a cyan rounded rectangular outline containing three thick white horizontal preview lines, last line shorter. The two panes are next to each other with a small comfortable gap. Make the panes equal size, with very simple chunky forms and strong visual balance, occupying about 75% of the width and 70% of the height. Flat vector-like minimal professional icon, crisp geometry, excellent legibility at small sizes. Only one letter M, no other text. No gradients, no shadows, no mockup, no perspective, no scene, no watermark. Output one square icon asset, with blue background filling every corner.
```
