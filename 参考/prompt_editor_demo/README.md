# Prompt Editor Demo

Desktop LLM本体へ統合する前に、`?`記法とプロンプト編集操作を検証する独立試作アプリです。

## 起動

```powershell
uv run prompt-editor-demo
```

## 基本操作

- `Tab`: `?`記法を補完。対象がなければ、通常文字で終わる行末を補完
- `Shift+Tab`: リストを1段階浅くする
- リストプリフィックスだけの行で`Backspace`: `- `を消して位置を維持。Enterで次行から再開し、もう一度Backspaceを押すと1段浅い`- `へ移動
- `Esc`: 進行中の補完をキャンセル
- `Ctrl+Z`: 補完全体を元に戻す
- `Ctrl+Enter`: 全文をクリップボードへコピー

既定では「疑似AI」が選択されています。「プロファイルAPI」へ切り替えると、Desktop LLMの既存SQLite設定、モデルプロファイル、推論値、keyring内のAPIキーを読み取ってOpenAI互換APIへ接続します。補完結果はDesktop LLMのチャット履歴へ保存しません。

## テスト

```powershell
uv run python -m pytest
```

短い実装範囲は[MVP仕様](MVP_SPEC.md)、将来案は[詳細仕様](SPEC.md)を参照してください。
