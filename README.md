# elidli-test-animation-model

用同一组素材，让不同模型制作动画，再对照原始作品与实际验证结果，建立自己的模型判断。

**开发版 `dev-0.3`**。这是一个可复用的动画测试 Skill 和本地记录工具，不是已经校准的模型排行榜。完整语义、审美和编程总分目前保留为 `null`，不让模型给自己的作品打分。

[查看演示帖](https://x.com/elidlicom/status/2115406447559512506) · [Skill 入口](SKILL.md) · [运行边界](references/runtime.md)

## 它怎么测

1. 你提供 **4 项素材**：文字、图片、网页或社交账号，也可以补充故事与画风要求。没有固定人物，不从上一轮自动借用素材。
2. 收齐素材后发题计时。默认要求 **15 分钟内**制作一段 **30 秒、1280×720** 的动画，交付一个可离线播放的 HTML 文件。
3. 模型先给设计稿，再独立编码。首次提交前不运行作品，不调用其他模型、子代理或图片/视频生成服务协助作答。
4. 冻结首次作品，再检查播放、暂停、重播、基本布局和实际网络请求等行为。默认不替模型修好答案；明确要求修复时另记一次尝试。
5. 交付作品、报告和复用题面。把 `exports/` 整体交给下一个模型，可沿用创作目标和用户素材；图片附件也要一起带走。

可以直接比较作品对要求的落实、剧情表达与视觉效果；可自动验证的行为另列证据，无法验证的项目明确保留未知。

## 安装与开始

以 Codex 的用户级 Skill 目录为例，在尚不存在同名目录时执行：

```bash
git clone https://github.com/litany-lty/elidli-test-animation-model.git ~/.codex/skills/elidli-test-animation-model
```

让客户端重新发现 Skill 后，输入：

```text
使用 $elidli-test-animation-model 测试当前模型。

本轮四项素材：
1. 戴红围巾的纸片机器人，必须保留红围巾。
2. 一只透明的蓝色鲸鱼，必须参与故事。
3. 悬浮在夜空中的图书馆。
4. 一枚失去指针的钟表。

主题：找回时间。其他设计由模型完成。
```

这是可替换的示例。你也可以只说“我要测试模型”，让 Skill 先收集你的素材。安装目录因客户端而异；其他客户端的自动发现与执行流程尚未逐一验证。

## 运行环境

- 本地记录器使用 Python 标准库和 Unix 文件锁，面向 **macOS / Linux**；Windows 原生运行未验证。
- 浏览器诊断需要 Python **Playwright + Pillow** 及 Playwright 对应的 Chromium。缺少它们仍可保留作品，但相关检查会标为未验证。
- 工具不会自动安装软件，也不要求模型 API Key，不替你切换模型或账号。

检查当前环境：

```bash
python3 ~/.codex/skills/elidli-test-animation-model/scripts/bench.py doctor
```

如需浏览器诊断且愿意安装依赖，可在自己的虚拟环境中手动执行：

```bash
cd ~/.codex/skills/elidli-test-animation-model
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python scripts/bench.py doctor
```

后续使用这个虚拟环境的 Python 执行记录工具。`requirements-browser.txt` 固定的是开发环境版本，不承诺所有平台的兼容性。

## 你会拿到什么

```text
RUN/
├── input/                    原始题面、素材与创作提示词
├── design/                   设计版本
├── evidence/                 本轮真实来源资料
├── submissions/attempt-01/   冻结的首次作品与诊断
├── results/                  HTML / Markdown 报告
└── exports/                  可转交下一模型的题面与图片
```

`RUN` 使用独立目录。完整命令与文件格式见 [workflow.md](references/workflow.md) 和 [SKILL.md](SKILL.md)。

## 怎样理解结果

- **作品离线，不等于取材过程完全离线。** 文字和用户图片不要求联网；网页、社交账号素材需要在预算内读取来源。
- **独立作答，不等于已隔离所有上下文。** Skill 无法清除客户端预先加载的系统规则、项目说明或历史。结果反映本轮模型、客户端和实际条件的组合；已知影响需要留档。
- **相同素材，不代表全部条件相同。** 比较时记录模型、客户端、输入方式与可用工具。模型名称由调用方声明，工具不验证提供方内部路由。
- **时间不可确认时就不填。** 跨机器、重启、启动标识不可读或时钟不连续时，耗时可以是 `null`。
- **浏览器检查不等于语义裁判。** 暂停画面没有变化，不能证明故事正确或作品好看。当前诊断的已确认权重不重新归一成满分。
- **不随仓库发布测试素材和运行记录。** 本地记录可能包含用户图片、网页摘录、机器名和时间；公开自己的测试结果前请先挑选需要分享的部分。

## 本地开发检查

```bash
python3 -B -m unittest discover -s scripts -p 'test_bench.py'
```

这些用例检查记录、冻结、素材复用和计时降级等行为，不是多模型能力测试。浏览器夹具的历史验证范围与自动评分门禁见 [runtime.md](references/runtime.md)。

## 许可

[MIT](LICENSE)，Copyright (c) 2026 litany-lty。Playwright、Pillow 等依赖沿用各自许可，不随本仓库分发；用户提供的素材不自动取得本项目的 MIT 授权。
