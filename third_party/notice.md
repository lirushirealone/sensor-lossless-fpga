# 第三方来源与权利边界

- 自研代码按根目录MIT许可提供；这不改变任何第三方代码或数据的原有许可。
- `runtime/third_party/zlib-1.2.11`：固定zlib 1.2.11源码子集，保留原始源码头部、README与source-manifest.json。来源 https://zlib.net/fossils/zlib-1.2.11.tar.gz 。原许可见zlib.h开头，不将其声明为本项目原创。运行时生成的deflate_capture.c是明确标记的宏钩子适配副本，不覆盖上游源文件。
- `build/ps7_preset.tcl`：由Xilinx/PYNQ v3.0.1的PYNQ-Z2预设提取，来源 https://github.com/Xilinx/PYNQ/tree/v3.0.1 ，BSD-3-Clause许可全文见PYNQ_license.txt。
- AMD Vivado、AXI DMA/Interconnect、Processing System 7等由用户合法安装的AMD工具提供，未分发工具本体或声称IP属于自研。
- NumPy、PYNQ、PySide6、pyqtgraph及Paramiko等采用各自许可，通过环境依赖安装，不把其作为自研源码打包。
- `installer`通过固定URL与SHA256下载uv 0.13.6（Astral，MIT或Apache-2.0），由其管理独立的python-build-standalone CPython及依赖；这些下载产物保留上游许可，不受本项目MIT重新授权。源码包不含uv、Python或Qt安装二进制。PySide6/Qt采用其上游许可，安装后保留依赖包随附的许可文件。
- 示例数据另遵循`data/sources.md`及catalog.json的许可与引用，不受根MIT许可重新授权。
