# 示例

可提交的小示例在 `samples/`：

- `samples/01_open_source` — 情形 A，附带开源代码
- `samples/02_no_code` — 情形 B，无代码
- `samples/03_pseudocode` — 情形 C，仅伪代码

本地跑一篇：

```bash
python -m paper2code run samples/01_open_source --llm offline
```

完整流水线产物写到 gitignore 的 `outputs/single/<paper_id>/`。不要把本机
`example/output/` 再提交进来：那是一次本地运行的转储（PDF、音频、图，以及带
本机绝对路径的安装脚本），不是示例数据。

分享生成的 `install_env.ps1`、`install_env.sh`、`repro_run.sh` 时，路径应相对
脚本所在目录（或用 `REPO_DIR` 覆盖）。不要把盘符路径写进仓库。

凭据只放在 gitignore 的 `configs/api_keys.yaml`。仓库里只保留
`configs/api_keys.yaml.example`。
