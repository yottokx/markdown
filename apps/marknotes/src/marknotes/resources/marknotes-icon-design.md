# MarkNotes 専用アイコン

2026-09-13、内蔵の `image_gen` を使用して新規作成。

- 旧Markdownエディタの「枠で囲ったM」と「左右に並んだカード」のモチーフを継承。
- 右側は二枚のノートを重ねた形にして、ノートを蓄積するアプリを表現。
- 深いプラム色の背景、クリーム色のM・本文線、琥珀色のノート枠で、青／シアンの旧エディタと区別する。
- `marknotes-icon.png`：採用した正方形の生成画像。背景は不透明。画像自体への後加工は行っていない。
- `marknotes-icon.ico`：16 / 20 / 24 / 32 / 40 / 48 / 64 / 128 / 256 pxを収録。
- アプリのウィンドウ・タスクバー・PyInstallerのEXEで共通使用。
- 従来の `app-icon.*` は取り込み元の記録として残し、アプリからの参照は専用アイコンへ変更した。元エディタのプロジェクトは変更していない。

ICO再作成：`uv run --locked python tools/build_app_icon.py`。

## 採用画像の生成プロンプト

内蔵ツールによる新規生成。既存アイコンを目視し、その特徴を文章で指定した。

```text
Use case: logo-brand.
Create exactly ONE finished square application icon for MarkNotes.

FULL BLEED OPAQUE SQUARE: the ENTIRE canvas is a continuous completely flat solid deep plum background (#562C46), filled to every edge and every corner. No transparency, no rounded outside tile, no outside backdrop, no alpha, no gradient, no texture, no shadows, no lighting, no vignetting, no artifacts. This is a simple crisp flat vector-like 2D graphic.

Center a bold two-part symbol occupying about 84% of the canvas width and 58% of its height. On the left, a large warm ivory (#FFF4E3) rounded rectangular outline encloses one thick geometric uppercase M in the same ivory. On the right, a golden amber (#F3B55D) rounded rectangular outline encloses three thick ivory horizontal lines, with the bottom line shorter. Behind this right rectangle there is exactly one amber offset top-and-right outline, indicating a simple stack of two notes. The stacked notes and M panel should be equally balanced. Leave a clear gap between the left M panel and right note stack. Make both panels center vertically and keep all strokes chunky, even and legible at 16px.

Design relationship: a sibling to a Markdown editor icon with a white M panel and cyan preview panel on dark blue. Keep that M-and-two-panels visual family, but this new icon MUST have a plum, cream and amber palette, and the note stack uniquely identifies MarkNotes.

Text: only the single exact uppercase letter "M". No other letters, no words.
Do not depict an app interface, devices, mockups or a scene. No logo presentation sheet, no border around the canvas. No blue or cyan. The entire square background must be flat, uniformly opaque plum to ALL FOUR CORNERS. Single square PNG asset.
```


## 検証

PNGは1254×1254の不透明画像。ICOとQIconの16〜256pxの9サイズを確認し、Windows AppUserModelIDはYotto.MarkNotesで維持した。

PyInstallerビルド後、配布フォルダのPNG/ICOが元リソースと一致し、EXEに埋め込まれた全9サイズの画像データも新ICOとバイト単位で一致することを確認した。画面やEXEは起動せず、アイコン資産・参照・埋め込みの検証だけを行った。

検証記録：`artifacts/icon-design/icon-validation.json`、`exe-icon-validation.json`、`build.log`。