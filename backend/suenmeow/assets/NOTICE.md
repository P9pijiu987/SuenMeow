# DeepSeek offline tokenizer data

`deepseek_v4_tokenizer.json` is the data file from the official
[Token & Token Usage documentation](https://api-docs.deepseek.com/quick_start/token_usage/),
[download](https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip), retrieved 2026-10-13.
SHA256: `89085f12ef79460ac5f66d1119325ddfc694b4ab209d80bbd81d35f081dc9614`.
Only JSON vocabulary data is loaded through `tokenizers`; the example Python and
chat template are not executed. No runtime download or remote code is used.
Counts include a framing margin and are estimates, not exact provider billing.
