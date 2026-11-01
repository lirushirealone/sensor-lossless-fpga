# 自主选题初级组材料对照

依据2026年11月1日读取的Gitee仓库TRACK_GUIDE_2026.md第3.3节。源链接：https://gitee.com/Vickyiii/fpgachina26-amd/blob/master/TRACK_GUIDE_2026.md 。赛事最终公告与后台要求优先；本表是工程对照，不代替资格审核。

| 指南内容 | 本作品位置与核查范围 |
|---|---|
| 板卡器件与实测 | PYNQ-Z2 xc7z020clg400-1，evidence/release_checks.json，硬件测量仍使用原日期 |
| 源码和上板程序 | src/rtl、runtime/board、runtime/unified-overlay、tools/run_board.py |
| 仿真与实现证据 | sim、build、evidence/hardware及历史release_checks |
| 从零复现与其他布局说明 | README.md目录映射、安装、CLI和重新构建步骤 |
| 大模型交互与纠错 | report/ai_records/records.md和records.json，经筛选原话而非完整聊天全集 |
| 技能包 | skill/README.md及案例来源、验证、失效条件；不虚报跨板加分资格 |
| 开源与许可 | GitHub公开仓库，项目MIT，第三方原许可保留 |
| 正式技术报告 | 官网PDF与可编辑Word另随提交材料交付，并与本轮源码提交编号一致 |

仓库内没有中文或空格路径，程序界面及安装后生成的中文用户目录不进入源码包。指南正文提出小写命名，但其自身示例也使用README.md；本包保留README、LICENSE和既有说明文档、第三方许可的惯用大写命名，其他新文件均为小写。若赛事要求所有说明文件也必须小写，应统一改引用后重新校验，不把这一歧义写成已经获得官方豁免。

本轮未重新测量硬件性能、功耗、ASIC/PPA或实体ADC；现有独立安装只在同一实体电脑的新环境验证，第二台实体电脑仍未验收。
