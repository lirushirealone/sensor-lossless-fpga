# 证据范围

historical_summary.json从此前独立核验与正式实现记录提取；tested_on标记原测量日期。硬件报告只遮去主机名称，不改变资源或时序数值；转换前后哈希见source_provenance.json。

src/rtl的六份文件与正式设计逐字节相同。软件来源、复制时SHA与提交SHA逐项保留；GUI打包适配和预设密码清理不伪装成零修改。完整仓库哈希在manifest.json。

data/reference是实际正式板端输出，verify_release将其逐点解码与公开整数源比较。这只能核验保存结果，不是打包日新硬件运行。sim的黄金包哈希来自原并行端点证据，新仿真需要重新运行脚本。PS7/完整AXI CDC不由端点仿真代替。

本轮新检查结果生成在results中，再按实际范围汇总为release_checks.json；未运行项目必须标未运行，不继承历史通过标签。既有完整实验档案保留在原开发工程，仓库只带必要复现样例、原报告和摘要。
