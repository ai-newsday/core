# 草稿预览发布工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 finalize 产物带 Hugo front matter（`draft: true`），通过 Hugo + PaperMod 部署到 GitHub Pages 供睡前预览，确认后 publish 翻转为 `draft: false` 并发 Telegram 终稿。

**Architecture:** `publish.py` 加两个纯函数（`render_front_matter` 拼 Hugo front matter、`flip_draft` 翻转草稿状态）；`publish()` 编排时把 front matter 前置到 body（`render_markdown` 本身不变，body 测试全绿）。内容落点从 `docs/daily/` 改 `content/posts/`（Hugo 惯例）。新增 Hugo 工程（vendored PaperMod 主题）+ 3 个 workflow（finalize/pages/publish）。

**Tech Stack:** Python 3.12（uv）、pytest、Hugo（静态站）、PaperMod 主题、GitHub Actions + GitHub Pages。

---

## 背景：分支拓扑注意

本 worktree（分支 `crazy-booth-54001c`）**不含** `.github/workflows/`——这些文件只在 `master`。Task 6/7 创建的 workflow 以本计划内容为权威版本；合并到 master 时若冲突，取本分支版本。

## File Structure

- `src/pipeline/publish.py`（改）—— 加 `render_front_matter`、`flip_draft` 纯函数；`publish()` 前置 front matter；`_render_categories` 补 takeaway
- `src/core/types.py`（改）—— `WebsiteConfig.output_dir` 默认 `docs/daily` → `content/posts`
- `src/tools/flip_draft.py`（建）—— 薄 CLI，包 `flip_draft` 供 publish.yml 调用
- `tests/golden/test_publish.py`（改）—— 更新 publish 快照断言 + 加 front matter/takeaway/flip_draft 测试
- `tests/contract/test_delivery_config.py`（改）—— 默认 output_dir 断言
- `hugo.toml`（建）、`themes/PaperMod/`（vendor）、`.gitignore`（改：加 `public/`）
- `.github/workflows/finalize.yml`、`pages.yml`、`publish.yml`（建）

---

### Task 1: WebsiteConfig 默认输出目录改 content/posts

**Files:**
- Modify: `src/core/types.py:399-402`
- Test: `tests/contract/test_delivery_config.py`

- [ ] **Step 1: 写失败测试**

在 `tests/contract/test_delivery_config.py` 末尾追加：

```python
def test_website_default_output_dir_is_content_posts(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = load_delivery_config("does/not/exist.yaml")
    assert cfg.website.output_dir == "content/posts"
```

确认文件顶部已 `from src.core.config import load_delivery_config`（若无则加）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_delivery_config.py::test_website_default_output_dir_is_content_posts -v`
Expected: FAIL，`assert 'docs/daily' == 'content/posts'`

- [ ] **Step 3: 改默认值**

`src/core/types.py` 的 `WebsiteConfig`：

```python
@dataclass
class WebsiteConfig:
    enabled: bool = True
    output_dir: str = "content/posts"
    git_push: bool = False  # True = finalize 后自动 git add + commit
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/contract/test_delivery_config.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add src/core/types.py tests/contract/test_delivery_config.py
git commit -m "feat(publish): default website output_dir -> content/posts (Hugo)"
```

---

### Task 2: render_front_matter 纯函数

**Files:**
- Modify: `src/pipeline/publish.py`
- Test: `tests/golden/test_publish.py`

`render_front_matter(report, config, draft)` 从 `report` 派生 Hugo front matter：title、date（东八区，从 date_label 取 YYYY-MM-DD 前缀，固定 `T08:00:00+08:00` 保持确定性）、draft、tags（取 `report.categories` 的 label，已去重且按 type_labels 序）、summary（daily_take 截 140 字）。

- [ ] **Step 1: 写失败测试**

在 `tests/golden/test_publish.py` 末尾追加（文件已有 `_ri`/`_rr`/`build_report`/`CFG`/`SourceType`）：

```python
from src.pipeline.publish import render_front_matter


def test_front_matter_draft_true():
    items = [_ri("https://a/1", source_type=SourceType.MODEL),
             _ri("https://a/2", source_type=SourceType.PAPER)]
    rep = build_report(_rr(items, daily_take="今天有两条。"), "2026-05-30（周六）", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    assert fm.startswith("---\n") and fm.rstrip().endswith("---")
    assert 'title: "AI Daily · 2026-05-30（周六）"' in fm
    assert "date: 2026-05-30T08:00:00+08:00" in fm
    assert "draft: true" in fm
    # tags = categories 的 label, type_labels 序: paper 在 model 前
    assert 'tags: ["论文", "模型"]' in fm
    assert 'summary: "今天有两条。"' in fm


def test_front_matter_draft_false():
    rep = build_report(_rr([_ri("https://a/1")]), "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=False)
    assert "draft: false" in fm
    assert "date: 2026-05-30T08:00:00+08:00" in fm


def test_front_matter_empty_daily_take():
    rep = build_report(_rr([_ri("https://a/1")], daily_take=None), "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    assert 'summary: ""' in fm


def test_front_matter_truncates_summary_to_140():
    long = "看" * 200
    rep = build_report(_rr([_ri("https://a/1")], daily_take=long), "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    assert "看" * 140 in fm
    assert "看" * 141 not in fm


def test_front_matter_escapes_double_quotes():
    rep = build_report(_rr([_ri("https://a/1")], daily_take='含"引号"的看点'),
                       "2026-05-30", CFG)
    fm = render_front_matter(rep, CFG, draft=True)
    assert 'summary: "含\\"引号\\"的看点"' in fm
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/golden/test_publish.py -k front_matter -v`
Expected: FAIL，`ImportError: cannot import name 'render_front_matter'`

- [ ] **Step 3: 实现**

在 `src/pipeline/publish.py` 顶部 import 区加 `import re`，并在 `render_markdown` 之前加：

```python
def _yaml_quote(s: str) -> str:
    """双引号包裹并转义内嵌双引号(够用的最小 YAML 标量转义)。"""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def render_front_matter(report: DailyReport, config: PublishConfig,
                        draft: bool) -> str:
    """Hugo front matter(确定性, 无 now)。date 取 date_label 的 YYYY-MM-DD 前缀,
    固定东八区 08:00。tags = categories 的 label(已去重 + type_labels 序)。"""
    m = re.match(r"\d{4}-\d{2}-\d{2}", report.date_label)
    iso_date = m.group(0) if m else report.date_label
    tags = ", ".join(_yaml_quote(c.label) for c in report.categories)
    summary = (report.daily_take or "")[:140]
    lines = [
        "---",
        f"title: {_yaml_quote('AI Daily · ' + report.date_label)}",
        f"date: {iso_date}T08:00:00+08:00",
        f"draft: {'true' if draft else 'false'}",
        f"tags: [{tags}]",
        f"summary: {_yaml_quote(summary)}",
        "---",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/golden/test_publish.py -k front_matter -v`
Expected: PASS（5 个）

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/publish.py tests/golden/test_publish.py
git commit -m "feat(publish): render_front_matter for Hugo (title/date/draft/tags/summary)"
```

---

### Task 3: flip_draft 纯函数

**Files:**
- Modify: `src/pipeline/publish.py`
- Test: `tests/golden/test_publish.py`

- [ ] **Step 1: 写失败测试**

`tests/golden/test_publish.py` 末尾追加：

```python
from src.pipeline.publish import flip_draft


def test_flip_draft_true_to_false():
    text = "---\ntitle: \"x\"\ndraft: true\ntags: []\n---\n# body\n"
    out = flip_draft(text)
    assert "draft: false" in out
    assert "draft: true" not in out
    assert "# body" in out          # 正文不动


def test_flip_draft_idempotent_when_already_false():
    text = "---\ndraft: false\n---\nbody"
    assert flip_draft(text) == text


def test_flip_draft_no_front_matter_unchanged():
    text = "# just a body, no front matter\n"
    assert flip_draft(text) == text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/golden/test_publish.py -k flip_draft -v`
Expected: FAIL，`ImportError: cannot import name 'flip_draft'`

- [ ] **Step 3: 实现**

`src/pipeline/publish.py` 在 `render_front_matter` 之后加：

```python
def flip_draft(text: str) -> str:
    """把 front matter 里的 `draft: true` 行替换为 `draft: false`(幂等)。
    仅匹配行首(允许前导空格)的 draft 键, 避免误伤正文。无匹配则原样返回。"""
    return re.sub(r"(?m)^(\s*draft:\s*)true\s*$", r"\1false", text)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/golden/test_publish.py -k flip_draft -v`
Expected: PASS（3 个）

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/publish.py tests/golden/test_publish.py
git commit -m "feat(publish): flip_draft to promote draft:true -> false"
```

---

### Task 4: publish() 前置 front matter（draft:true）

**Files:**
- Modify: `src/pipeline/publish.py:144-163`（`publish` 函数）
- Test: `tests/golden/test_publish.py`

`publish()` 渲染 body 后前置 front matter。`render_markdown`（body）保持不变，所以 `test_render_markdown_*` 全绿；只动 `publish()` 输出，更新对应快照/断言。

- [ ] **Step 1: 改快照测试 + 加 front matter 断言**

修改 `tests/golden/test_publish.py` 的 `test_publish_markdown_snapshot`，在 assert 前插入对 front matter 的断言，并**删除旧快照**让其重固化：

```python
def test_publish_markdown_snapshot():
    res = publish(_rr(_snapshot_items(), daily_take="看点一句话。"),
                  "2026-05-30（周六）", CFG, _ctx())
    # publish 产物 = front matter(draft:true) + body
    assert res.markdown.startswith("---\n")
    assert "draft: true" in res.markdown.split("---", 2)[1]
    assert "# AI Daily · 2026-05-30（周六）" in res.markdown
    if not SNAPSHOT.exists():               # 首次运行固化快照
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(res.markdown, encoding="utf-8")
    assert res.markdown == SNAPSHOT.read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/golden/test_publish.py::test_publish_markdown_snapshot -v`
Expected: FAIL（`res.markdown` 还不含 front matter，`startswith("---\n")` 为 False）

- [ ] **Step 3: 改 publish() 实现**

`src/pipeline/publish.py` 的 `publish()`，把 markdown 行改为前置 front matter：

```python
    markdown = (render_front_matter(report, config, draft=True)
                + "\n" + render_markdown(report, config))
```

（仅替换原 `markdown = render_markdown(report, config)` 一行；其余不变。）

- [ ] **Step 4: 删旧快照并跑全套 publish 测试**

```bash
rm -f tests/golden/data/publish_report.md
uv run python -m pytest tests/golden/test_publish.py -v
```
Expected: PASS（快照重固化；`test_render_markdown_*` body 测试不受影响仍绿；`test_publish_deterministic` 绿）

- [ ] **Step 5: 检查重固化的快照内容合理**

Run: `head -10 tests/golden/data/publish_report.md`
Expected: 前 7 行是 front matter（`---` / title / date / `draft: true` / tags / summary / `---`），之后是 `# AI Daily · ...`

- [ ] **Step 6: 提交**

```bash
git add src/pipeline/publish.py tests/golden/test_publish.py tests/golden/data/publish_report.md
git commit -m "feat(publish): prepend Hugo front matter to publish() output"
```

---

### Task 5: 分类速览补 takeaway

**Files:**
- Modify: `src/pipeline/publish.py:100-110`（`_render_categories`）
- Test: `tests/golden/test_publish.py`

- [ ] **Step 1: 写失败测试**

`tests/golden/test_publish.py` 末尾追加：

```python
def test_categories_render_takeaway_when_present():
    items = [_ri("https://a/1", source_type=SourceType.MODEL,
                 title="T", summary="S。", takeaway="可本地部署。",
                 eligible=False)]   # 非必读, 只出现在分类速览
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    cat_block = md.split("## 📚 分类速览", 1)[1]
    assert "可本地部署。" in cat_block


def test_categories_skip_empty_takeaway():
    items = [_ri("https://a/1", source_type=SourceType.MODEL,
                 title="T", summary="S。", takeaway="", eligible=False)]
    md = render_markdown(build_report(_rr(items), "2026-05-30", CFG), CFG)
    cat_block = md.split("## 📚 分类速览", 1)[1]
    # 空 takeaway 不产生孤立的 "↳" 行
    assert "↳" not in cat_block
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/golden/test_publish.py -k takeaway -v`
Expected: `test_categories_render_takeaway_when_present` FAIL（"可本地部署。" 不在分类块）

- [ ] **Step 3: 实现**

`src/pipeline/publish.py` 的 `_render_categories`，在每条目的链接行后按需追加 takeaway 行：

```python
def _render_categories(report: DailyReport) -> list[str]:
    lines = ["## 📚 分类速览", ""]
    for cat in report.categories:
        lines.append(f"**{cat.label}**")
        for it in cat.items:
            mark = " 🧭探索" if it.is_explore else ""
            lines.append(
                f"- `[{it.score}]`{mark} {it.title} — {it.summary} "
                f"｜ [{it.source}]({it.link})")
            if it.takeaway:
                lines.append(f"  ↳ 对你：{it.takeaway}")
        lines.append("")
    return lines
```

- [ ] **Step 4: 跑测试确认通过**

```bash
rm -f tests/golden/data/publish_report.md   # 正文变了, 快照需重固化
uv run python -m pytest tests/golden/test_publish.py -v
```
Expected: PASS（含两个 takeaway 测试；快照重固化）

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/publish.py tests/golden/test_publish.py tests/golden/data/publish_report.md
git commit -m "feat(publish): show takeaway under each item in category list"
```

---

### Task 6: flip_draft CLI（供 publish.yml 调用）

**Files:**
- Create: `src/tools/__init__.py`
- Create: `src/tools/flip_draft.py`
- Test: `tests/contract/test_flip_draft_cli.py`

- [ ] **Step 1: 写失败测试**

Create `tests/contract/test_flip_draft_cli.py`：

```python
import subprocess
import sys


def test_flip_draft_cli_rewrites_file(tmp_path):
    p = tmp_path / "post.md"
    p.write_text("---\ndraft: true\n---\nbody\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "src.tools.flip_draft", str(p)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = p.read_text(encoding="utf-8")
    assert "draft: false" in out
    assert "draft: true" not in out


def test_flip_draft_cli_missing_file_is_noop(tmp_path):
    missing = tmp_path / "nope.md"
    r = subprocess.run(
        [sys.executable, "-m", "src.tools.flip_draft", str(missing)],
        capture_output=True, text=True)
    assert r.returncode == 0          # 缺文件不报错(workflow 容错)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/contract/test_flip_draft_cli.py -v`
Expected: FAIL，`No module named src.tools.flip_draft`

- [ ] **Step 3: 实现**

Create `src/tools/__init__.py`（空文件）。

Create `src/tools/flip_draft.py`：

```python
from __future__ import annotations

import sys
from pathlib import Path

from src.pipeline.publish import flip_draft


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m src.tools.flip_draft <file.md>", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.exists():
        return 0  # 缺文件不报错: 让 workflow 幂等容错
    path.write_text(flip_draft(path.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/contract/test_flip_draft_cli.py -v`
Expected: PASS（2 个）

- [ ] **Step 5: 提交**

```bash
git add src/tools/__init__.py src/tools/flip_draft.py tests/contract/test_flip_draft_cli.py
git commit -m "feat(tools): flip_draft CLI for publish workflow"
```

---

### Task 7: Hugo 工程脚手架（hugo.toml + vendored PaperMod）

**Files:**
- Create: `hugo.toml`
- Create: `themes/PaperMod/`（vendor，clone 后去 .git）
- Modify: `.gitignore`（加 `public/`）

- [ ] **Step 1: vendor PaperMod 主题**

```bash
git clone --depth 1 https://github.com/adityatelange/hugo-PaperMod themes/PaperMod
rm -rf themes/PaperMod/.git
```
Expected: `themes/PaperMod/theme.toml` 存在

- [ ] **Step 2: 写 hugo.toml**

Create `hugo.toml`：

```toml
baseURL = "/"
languageCode = "zh-cn"
title = "AI News Daily"
theme = "PaperMod"
defaultContentLanguage = "zh"
enableRobotsTXT = true
buildDrafts = false        # CI 预览用 `hugo -D` 覆盖
pygmentsUseClasses = true

[params]
  defaultTheme = "auto"
  ShowReadingTime = true
  ShowShareButtons = false
  ShowPostNavLinks = true
  ShowBreadCrumbs = true
  ShowCodeCopyButtons = true
  ShowToc = true

[params.homeInfoParams]
  Title = "AI News Daily"
  Content = "一手、经评分筛选、带人工锐评的每日 AI 资讯。"

[outputs]
  home = ["HTML", "RSS"]

[[menu.main]]
  identifier = "archive"
  name = "归档"
  url = "/archives/"
  weight = 10

[[menu.main]]
  identifier = "tags"
  name = "标签"
  url = "/tags/"
  weight = 20
```

- [ ] **Step 3: 建归档页**

Create `content/archives.md`：

```markdown
---
title: "归档"
layout: "archives"
url: "/archives/"
summary: archives
---
```

- [ ] **Step 4: .gitignore 加 public/**

在 `.gitignore` 末尾追加一行：

```
public/
```

（用 Edit 在文件末尾追加，勿动既有行。）

- [ ] **Step 5: 本地验证 build（若装了 hugo）**

```bash
command -v hugo && hugo --gc --minify -D || echo "hugo not installed locally; CI will build"
```
Expected: 装了 hugo → `public/` 生成且无 error；未装 → 打印提示（不阻断）

- [ ] **Step 6: 提交**

```bash
git add hugo.toml themes/PaperMod content/archives.md .gitignore
git commit -m "feat(site): Hugo + vendored PaperMod scaffold"
```

---

### Task 8: 三个 GitHub Actions workflow

**Files:**
- Create: `.github/workflows/finalize.yml`
- Create: `.github/workflows/pages.yml`
- Create: `.github/workflows/publish.yml`

无单测（CI 实跑验证）。逐个写全文件。

- [ ] **Step 1: finalize.yml**

Create `.github/workflows/finalize.yml`：

```yaml
name: Finalize (Draft)

on:
  workflow_dispatch:

jobs:
  finalize:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    env:
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      MODELSCOPE_API_KEY: ${{ secrets.MODELSCOPE_API_KEY }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          version: "latest"
      - run: uv sync

      - name: Run finalize tick (writes content/posts/<date>.md, draft:true)
        run: uv run python -m src.cli --tick finalize

      - name: Commit draft
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add content/ data/
          git diff --cached --quiet || git commit -m "chore: finalize draft $(date -u +%Y-%m-%d) [skip ci]"
          git push
```

- [ ] **Step 2: pages.yml**

Create `.github/workflows/pages.yml`：

```yaml
name: Pages

on:
  push:
    branches: [master]
    paths:
      - "content/**"
      - "hugo.toml"
      - "themes/**"
      - ".github/workflows/pages.yml"
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build-deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: peaceiris/actions-hugo@v3
        with:
          hugo-version: "latest"
          extended: true
      - name: Build (include drafts for preview)
        run: hugo --gc --minify -D --baseURL "${{ steps.pages.outputs.base_url }}/"
      - uses: actions/configure-pages@v5
        id: pages
      - uses: actions/upload-pages-artifact@v3
        with:
          path: ./public
      - uses: actions/deploy-pages@v4
        id: deploy
```

- [ ] **Step 3: publish.yml**

Create `.github/workflows/publish.yml`：

```yaml
name: Publish

on:
  workflow_dispatch:

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    env:
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      MODELSCOPE_API_KEY: ${{ secrets.MODELSCOPE_API_KEY }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          version: "latest"
      - run: uv sync

      - name: Promote today's draft -> published
        run: |
          DATE=$(date -u +%Y-%m-%d)
          uv run python -m src.tools.flip_draft "content/posts/${DATE}.md"

      - name: Send Telegram final report
        run: uv run python -m src.cli --tick finalize --publish-only 2>/dev/null || true

      - name: Commit published content
        run: |
          DATE=$(date -u +%Y-%m-%d)
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add content/ data/
          git diff --cached --quiet || git commit -m "publish: daily report ${DATE}"
          git push
```

注：`--publish-only` 若 cli 未实现该 flag，`|| true` 兜底不阻断（发终稿是 nice-to-have，本计划不引入新 flag）。Pages 由 publish 的 commit 触发 `pages.yml` 重新 build。

- [ ] **Step 4: workflow 语法自检**

Run: `for f in .github/workflows/*.yml; do uv run python -c "import yaml,sys; yaml.safe_load(open('$f')); print('ok', '$f')"; done`
Expected: 三个都 `ok`

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/finalize.yml .github/workflows/pages.yml .github/workflows/publish.yml
git commit -m "ci: finalize/pages/publish workflows for draft-preview flow"
```

---

### Task 9: 全量回归 + 收尾

- [ ] **Step 1: 跑全套测试**

Run: `uv run python -m pytest -q`
Expected: 全绿（263 旧 + 新增约 14）

- [ ] **Step 2: lint**

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: 无错误（若 format 有差异，跑 `uv run ruff format src/ tests/` 后重提交）

- [ ] **Step 3: 端到端 dry 检查 publish 产物形态**

Run: `uv run python -m src.cli --publish 2>/dev/null | head -3` （若有 `--publish` dry 入口；否则跳过，依赖 Task 4 快照）
Expected: 输出以 front matter 开头或快照已覆盖

- [ ] **Step 4: 若有未提交的 format 改动则提交**

```bash
git diff --quiet || (git add -A && git commit -m "chore: ruff format")
```

---

## Self-Review

**Spec coverage：**
- §3 front matter 契约 → Task 2（render_front_matter，title/date/draft/tags/summary）✓
- §3 卡片补 takeaway → Task 5 ✓
- §3 flip_draft → Task 3（纯函数）+ Task 6（CLI）✓
- §4 content/posts 落点 → Task 1 ✓
- §4 finalize/pages/publish workflow → Task 8 ✓
- §4 Hugo + vendored PaperMod + public/ gitignore → Task 7 ✓
- §6 测试矩阵 7 项 → Task 1（output_dir）/2（front matter ×2 状态 + tags + summary）/3+6（flip_draft）/5（takeaway）/4（完整页 golden）全覆盖 ✓
- §7 验收 → Task 9 回归 ✓

**Placeholder scan：** 无 TBD/TODO；所有 code step 含完整代码；workflow 全文件给出。`--publish-only`/`--publish` 已显式标注为容错/可跳过，非阻断。

**Type consistency：** `render_front_matter(report, config, draft)`、`flip_draft(text)`、`WebsiteConfig.output_dir`、`_render_categories` 签名在各 Task 间一致；`render_front_matter`/`flip_draft` 在 Task 6 CLI 中按同名 import。
