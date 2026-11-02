# 多通道传感数据无损压缩系统

Sensor Lossless FPGA：基于PYNQ-Z2 / Zynq-7020的FPGA与ARM协同无损压缩系统。正式算法版本`unified-v1`，源码提交版`source-v4`。

一份Overlay同时运行四路1024点预测/Rice/四点分组引擎和8192点LZ77引擎。ARM保留完整候选比较、动态Huffman、最终择优、UCF1组帧和校验；不是全硬件DEFLATE。支持原始回退、独立帧CRC、元数据CRC、序号和总点数检查。当前应用是公开传感数据文件回放，不是实体ADC连续采集。

## 仓库布局

| 目录 | 内容 |
|---|---|
| src/rtl | 与正式设计逐字节相同的6份最终RTL |
| runtime/board、runtime/research | 完整活动驱动/算法依赖及软件契约测试；保留原模块布局以避免改动算法 |
| runtime/desktop_gui | 唯一桌面界面源码，已合并全部当前功能；软件包适配位于tools/app_config.py |
| runtime/unified-overlay | 经过上板验证的bit/hwh；哈希见manifest及历史证据 |
| sim | 独立双时钟端点仿真、固定输入和黄金输出哈希 |
| build | Vivado Tcl、PYNQ-Z2 PS7预设与独立构建入口 |
| board | 板端依赖说明及发布清单 |
| data | 3份公开真实示例、来源/许可和参考压缩文件 |
| tools | 完整性检查、软件测试、板端CLI、部署及桌面入口 |
| skill | 可复用核验脚本、DMA排错规则与提示词工作流 |
| report | 正式设计报告PDF与可编辑DOCX、报告数值证据、技术交互记录及设计说明 |
| evidence | 真实历史实现/性能报告、独立核验和技能验证证据 |

该布局对应AMD推荐目录，runtime用于保持已验证模块的相对路径。仓库文件使用英文名称，中文用于正文与界面。第三方依赖安装到本机环境，不包含Windows Python安装包、密码、SSH主机密钥、私人历史结果和原始聊天全集。

## 电脑上的最小验证

Python 3.12（桌面版验证环境）或兼容的Python 3.10+（仅解码/测试），安装NumPy：

```text
python -m venv .venv
# 激活该环境后执行：
python -m pip install -r requirements.txt
python tools/verify_release.py
python tools/run_tests.py
python tools/check_native.py
python tools/run_board.py decompress --input data/reference/power01.ucf --output results/power01_restored.npz
```

verify_release对三个参考压缩文件完整解码并逐点核对源NPZ，而非抽样。软件测试中的DMA模拟验证生命周期/协议，不能作为真实硬件性能数据。

## 板端直接运行

使用已有PYNQ-Z2镜像（已验证PYNQ 3.1.1）、镜像的NumPy和可用GCC。将整个仓库复制到板上，以该目录为工作目录；镜像的PYNQ Python路径如不同请调整命令。

```text
sudo /usr/local/share/pynq-venv/bin/python tools/run_board.py compress --input data/examples/power01.npz --output results/power01.ucf --group-size 2
python tools/run_board.py decompress --input results/power01.ucf --output results/power01_restored.npz
```

首次运行从随包C源码构建主机架构的辅助库，不复用Windows DLL到ARM。压缩后再次对照原输入全量复核，失败保留.part，不发布为成功文件。已有输出不覆盖。一次只让一个程序拥有该板卡；使用CLI前正常结束其他板卡任务。

## Windows双击安装

将源码包完整解压后，双击根目录的`install.cmd`。适用Windows 10/11 x64；首次需联网下载已固定并校验的安装工具、独立Python与依赖，不要求预装Python或Vivado，不改系统Python。安装完成创建桌面和开始菜单快捷方式。默认安装到当前用户的LocalAppData/Programs/SensorLosslessFPGA。详细说明与修复方法见installer/readme.md。

程序复制到独立安装目录，安装后可移走原始源码包；导入数据、历史结果汇总和工作缓存位于安装根目录。板卡IP、账号与SSH公钥须在另一台电脑正常配置，不能复制本机私钥或登录密码。

## 从电脑使用图形界面

桌面依赖为已验证的Python 3.12版本组合：

```text
python -m pip install -r requirements-desktop.txt
python tools/desktop.py
```

先通过正常SSH工具确认目标板卡的主机公钥并登录一次，使其进入当前用户的known_hosts；不使用AutoAddPolicy或关闭主机密钥校验。界面中输入板卡IP、用户名和密码，凭证不写入仓库。点击“检查连接”将当前清单上传到板端全新的内容寻址目录，不加载FPGA；点击“开始压缩”才运行真实板卡任务。解压与历史回看在电脑完成。用户结果只写results/下，已被Git忽略。

也可仅部署：`python tools/deploy.py --host <board_ip>`，密码交互输入或用进程环境PYNQ_PASSWORD提供。预设使用标准xilinx用户镜像，PYNQ辅助Python路径为/usr/local/share/pynq-venv/bin/python。部署复制完整当前依赖，不要求存在任何旧实验目录，不覆盖此前正式版本。板卡关机是界面中的独立确认操作，不作为自动测试动作。

## RTL仿真与重新构建

已验证工具Vivado 2026.1；推荐从工具终端运行，或显式指定安装路径：

```text
python sim/run_sim.py --tools <vivado_bin_directory>
python build/run_build.py --vivado <vivado_executable>
```

仿真默认种子0、1、17，分别验证134个LZ块/77包与467个原生帧/191包；外部脚本校验完整LZ包及计时一致性，再对照每个LZT1成员的黄金哈希；原生帧按完整集合比较，允许并行通道因背压改变完成顺序。PS7和AXI互连不在该端点仿真模型中，CDC与时序依据完整实现报告及板级实测。

构建脚本只需本仓库的RTL/Tcl，结果生成build/output/overlay/compressor.bit及同名hwh。禁止覆盖已有构建目录；可指定新的--work。使用新bit时以CLI的--bitfile指定对应文件，同目录必须有同名hwh。不要求重新构建位流与历史bit字节相同，但必须检查时序、资源、接口和功能。预构建位流与历史生产SHA保持一致。

## 结果边界

历史正式设计：LUT20669（38.85%）、FF24515（23.04%）、BRAM36等效49（35.00%）、DSP0；LZ77 66.666667MHz，原生50MHz；setup/hold余量0.247/0.023ns。资源是设计实现占用，不是实时忙碌率。

真实55记录统计和配对耗时见evidence/historical_summary.json。完整候选的统一版对代表记录比旧快速版更慢，不能用局部优化成绩宣称整机加速。8192点组块仍有采集等待；原始回退仅约束载荷，短文件加头后可以扩大。历史长跑的全局CmaFree变化来源未完全定位，不声称彻底排除所有内核内存泄漏。无功耗、ASIC/PPA或未知数据压缩率保证。

源码打包验证与历史证据分别记录，不把旧实测伪装成打包当天重测。请参阅report/limitations.md和evidence/readme.md。本RAR同时包含工程包、技能包和设计报告；report/design_report.pdf与官网“技术文档”栏使用同一份PDF。视频、授权书、照片和关键词仍按后台各自栏目提交。

## 许可

自研代码MIT；zlib、PYNQ预设及示例数据保留各自许可与引用，详见third_party/notice.md、data/sources.md。

## 历史板级验证（2026-10-31）

19项软件测试、134个原生C测试块、三种背压种子的双时钟RTL回归通过；离屏界面导入和解压选择通过。独立板端目录重新实跑三组示例、全量核对314034点，压缩字节与冻结参考一致。未重新进行完整布局布线；未执行板卡关机。完整范围见evidence/release_checks.json。

Windows原生检查编译四个运行辅助库；可选的entropy计时诊断器仅在Linux/板端测试，原诊断C的Windows头文件宏冲突尚未处理，不影响桌面界面或正式压缩。

## 当前源码与安装验证（2026-11-01）

桌面源码只保留runtime/desktop_gui，合并仍在使用的数据读取、板卡处理、关机检查与历史归档功能。新增install.cmd独立安装；21项软件检查、四个原生辅助库及134块参考通过。全新下载的Python环境中完成三份示例314034点完整还原、界面导入/解压、中文及空格路径、原源码移走后启动、重复安装保留历史、源码损坏拒绝和快捷方式目标核对。尚未在第二台实体电脑测试。

正式算法、驱动、RTL、位流、构建与仿真文件保持原字节；本轮不重新声称完成上板、布局布线或关机。完整范围及原硬件测量日期见evidence/release_checks.json。

## 大模型协作和技能材料补齐 2026年11月1日

真实技术交互摘录为report/ai_records/records.md与records.json，共10个案例；包含可见原话、纠错、验证及明确的脱敏说明，不是完整聊天全集。report/ai_collaboration.md为入口。历史报告与独立核验位于evidence/collaboration，各自保留原日期；其中原报告指向更大历史档案的链接不是本轮新实验，未附的原始板端逐包记录不能宣称已一并交付。

技能包补充原始案例来源、用途、验证效果、失效条件和迁移边界。python skill/check_examples.py可在任何兼容Python电脑上运行清单核验正反例；python tools/check_collaboration.py检查交互文本和证据指纹。这些步骤不连接板卡。source-v3只更新交付文档与核验材料，正式算法仍为unified-v1，安装器和算法文件保持source-v2字节。

## 三合一提交包 2026年11月2日

本RAR是自主选题提交内容的完整工程包，不只是程序代码。按后台“上传全部代码”栏目上传本RAR，其中同时包含：

1. 工程包：src/rtl、runtime、sim、build、board、data和tools；完整实现报告在evidence/hardware，实际板测与参考对照见evidence、data/reference。
2. 技能包：skill/readme.md、提示词模板、DMA故障清单、计时审查、核验脚本及对应验证证据。
3. 设计报告：report/design_report.pdf与report/design_report.docx；同目录的metric_evidence.json、algorithm_comparison_evidence.json给出测量口径与原始证据指纹。大模型真实交互位于report/ai_records，入口为report/ai_collaboration.md。

官网独立的“技术文档”PDF与report/design_report.pdf逐字节相同。所有包内路径采用英文小写，不含中文或空格；readme.md与license也统一小写。根readme.md保留推荐目录对照，runtime是保持已验证模块路径所需的实现目录，evidence承担推荐build/board目录中的历史报告与结果角色。

source-v4补齐正式报告并规范文件路径，压缩算法、RTL、C/Python运算代码与bit/hwh均保持原字节。第三方README文件只改为readme，内容和上游链接不变；相关清单键与指纹同步更新，不伪造新的硬件测试。已脱敏原始对话及归档实验报告不改写。
