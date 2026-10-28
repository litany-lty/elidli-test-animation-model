# 用户素材与记录格式 · dev-0.3

用户只需在聊天中提供素材。以下文件由 Agent 整理，不要求用户编写。

## materials.json

一个四元素数组。每项包含：

- id：依次为字符串 `1`、`2`、`3`、`4`。
- name：用户给定的名称或不改变原意的简称。
- type：`text`（文字）、`image`（用户图片）、`url`（普通网页参考）、`profile`（需要头像与近期推文推导的社交账号）。
- content：完整用户描述、链接或实际收到的图片文件路径。不能自行指定角色、账号或借用旧轮素材。
- requirement：用户的保留/使用要求；用户未加特殊要求时可写“用于本轮动画，发挥实际作用”。

issue --materials 会读取图片、保存只读副本和 SHA-256，将题面中的文件路径改为 `materials/编号.扩展名`；复制原图不修改内容。图片缺失会在创建运行和计时之前拒绝。复用题面必须与同目录下的 materials 附件一起转交；缺失/哈希不符则先索要正确附件。其他客户端没有本工具也能阅读题面和图片，但不能宣称已完成相同计时或评分。

`--prompt` 可提供用户已给的创作提示词；无此参数时采用开放创作要求。`--packet` 与 materials/prompt 互斥。未提供四项材料时，工具明确拒绝发题，不能用示例数据补齐。

## source-pack.json

为兼容记录结构，根数组仍名为 `characters`，**每项对应一个素材，不限定是人物**。四项 character_id 对应 `1` 到 `4`，不得添加或遗漏用户素材。

所有素材都有以下字段：

- source_urls：实际参考 URL 数组；直接用户文字可为空。
- retrieved_at：实际读取时间（带时区 ISO）；无法读取时 null。
- evidence_type：user-text、user-image、page、tool-output 或 unavailable 等实际来源说明。
- observed_features：用户明确给定或实际观察到的特征数组。
- adopted_description：完整采用描述，缺失时注明降级创作，不冒充成功取材。
- fictional_additions：额外剧情/造型设定数组；不可违背用户要求。
- uncertainties：缺失、访问失败和不能确认的信息数组。
- status：ACQUIRED、PARTIAL、UNAVAILABLE；只是资料声明，准确性仍需独立核验。
- evidence_ids：evidence 命令返回的未迟到证据 id 数组。文字原始输入已在题面保存，可为空。

仅 type=profile 的素材额外要求：

- avatar：url、viewed（是否实际看图）、observations（可见特征数组）、evidence_ids。
- recent_posts：至多 5 个对象，各含 url、published_at（带时区 ISO，未知 null）、observations（短摘录/主题数组）、evidence_ids。
- inference_notes：观察事实 → 创作推断的对应说明数组。

profile 声明 ACQUIRED 必须有已查看的头像证据、至少一条近 30 天推文及推导依据；只有简介不够。少于 5 条如实说明数量与覆盖不足；未知日期不能宣称符合近期窗口。不将纯转推、旧置顶帖算近期本人表达。头像插画只提炼视觉符号，不反推真人长相；推文主题可启发动画道具，不认定真实人格或敏感属性。

## 文件与计时

- input/original-task.md：实际发题，含用户四项要求；input/materials/ 保存原图副本。
- input/creative-prompt.md：独立同款创作目标；不夹带本轮推断或成绩。
- design/：各版本完整设计。submissions/attempt-NN/：冻结作品、设计、浏览器诊断和分数。
- evidence/sources/：必要原始资料，固定工具生成接收时间、指纹和迟到标记；不得伪造工具返回。
- results/report.html、report.md：作品和完整报告，缺资料明确标注。
- exports/reuse-test.md：主要复用题面；exports/materials/：必须随题面一起交付的图片。
- exports/reuse-original.md：实际输入存档；reuse-elements.md、reuse-design.md 是可选冻结比较，不能冒充默认重新取材流程。

收集用户材料与等待回复发生在 issue 之前，**不计时**。issue 校验/复制输入后记录起点。用户材料事先可见，timing_scope 为 first_tool_after_user_material_collection。首次作品提交即停止首次作答计时，验证/报告另记。跨主机、重启或明显时钟不连续时耗时 null，不猜测补填。修复保留首次结果并单独计时。

仅文字/链接的已收集素材可用 `export-reuse --materials materials.json --prompt creative-prompt.md --output reuse-test.md` 补做复用包；含图片时使用本轮 report 自动复制核验附件。补做文件不是旧轮实际输入，不覆盖原报告或成绩。
