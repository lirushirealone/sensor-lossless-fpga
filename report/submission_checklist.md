# 自主选题初级组材料对照

依据2026年11月1日读取的Gitee仓库TRACK_GUIDE_2026.md第3.3节。源链接：https://gitee.com/Vickyiii/fpgachina26-amd/blob/master/TRACK_GUIDE_2026.md 。赛事最终公告与后台要求优先；本表是工程对照，不代替资格审核。

| 指南内容 | 本作品位置与核查范围 |
|---|---|
| 板卡器件与实测 | PYNQ-Z2 xc7z020clg400-1，evidence/release_checks.json，硬件测量仍使用原日期 |
| 源码和上板程序 | src/rtl、runtime/board、runtime/unified-overlay、tools/run_board.py |
| 仿真与实现证据 | sim、build、evidence/hardware及历史release_checks |
| 从零复现与其他布局说明 | readme.md目录映射、安装、CLI和重新构建步骤 |
| 大模型交互与纠错 | report/ai_records/records.md和records.json，经筛选原话而非完整聊天全集 |
| 技能包 | skill/readme.md及案例来源、验证、失效条件；不虚报跨板加分资格 |
| 开源与许可 | GitHub公开仓库，项目MIT，第三方原许可保留 |
| 正式技术报告 | report/design_report.pdf、design_report.docx及同目录数值证据；外部技术文档PDF与包内PDF完全相同 |

所有包内文件与目录采用英文小写、数字、下划线、连字符和扩展名点号，不含中文、空格或其他字符；通用说明readme.md与license也统一小写。已同步当前文档、上板清单、第三方来源清单与安装校验中的引用。推荐结构非强制，具体映射见根readme.md。

本轮未重新测量硬件性能、功耗、ASIC/PPA或实体ADC；现有独立安装只在同一实体电脑的新环境验证，第二台实体电脑仍未验收。
