# 双击安装与使用

1. 把源码压缩包**完整解压**，不要在压缩包预览窗口直接运行。
2. 双击根目录的 `install.cmd`。适用 Windows 10/11 x64（Intel/AMD）；首次需联网访问 GitHub 与 Python 包下载站点。
3. 安装器自动下载已固定版本并核对SHA256的uv，准备独立Python 3.12.14及锁定依赖，完整核验三份参考数据，并检查桌面界面。电脑无需提前装Python、Jupyter或Vivado；日常运行也不需要安装器联网。
4. 完成后，通过桌面或开始菜单的“多通道传感数据无损压缩系统”启动。安装成功后，原始解压目录可以移动或删除。

默认位置：当前用户的 `%LOCALAPPDATA%\Programs\SensorLosslessFPGA`。其中 `app` 为程序，`导入数据` 为演示与输入文件，`历史结果汇总` 为验证通过的结果，`工作缓存` 和 `运行日志` 用于任务和排错。已有示例和历史不覆盖。可在PowerShell执行 `installer/install.ps1 -InstallRoot "你选择的目录"` 指定其他位置，目标不能位于源码包内部。

安装器不要求管理员权限，不改PATH、文件关联或已有Python。重复安装同一源码会复核并修复独立依赖；不同源码清单分开存放，只有新版本验证通过后才更新启动入口。安装过程中不要同时运行其他安装实例。

第一次在另一台电脑连接板卡时，须填写板卡IP和账号，并通过正常SSH工具核实板卡公钥；本软件不会携带或复制原电脑的密码、私钥和known_hosts。压缩需要已启动并接入网络的PYNQ-Z2；离线解压与历史查看可在电脑完成。

若安装失败，控制台会保留具体原因。检查网络能访问GitHub与PyPI、目标可写、源码完整解压，再重试。学校或单位禁用PowerShell、禁止下载程序的电脑需由管理员按本单位规则配置，不能保证绕过设备管理策略。ARM64、32位Windows、macOS和Linux的双击安装尚未验收；Linux板端继续使用README中的独立命令。

安装使用的公开工具说明：

- [uv安装与发布文件](https://docs.astral.sh/uv/getting-started/installation/)
- [独立管理Python](https://docs.astral.sh/uv/concepts/python-versions/)
- [固定依赖与同步环境](https://docs.astral.sh/uv/pip/compile/)
