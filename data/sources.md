# 示例与数据来源

文件是公开数据的整数数字采样值，未滤波、归一化、插值、裁剪或重新量化；NPZ列为ch0至ch3。参考UCF1文件是同样原值的已验证正式板端输出。全部数据哈希、采样率、通道哈希、数字表示与来源元数据见examples/catalog.json。

- power01：UCI Individual Household Electric Power Consumption。https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption 。贡献者Georges Hebrail、Alice Berard；原数据与许可说明按源页及catalog记录保留，CC BY 4.0。
- appliances_energy：UCI Appliances Energy Prediction。https://archive.ics.uci.edu/dataset/374/appliances+energy+prediction 。Luis Candanedo等人的公开数据，CC BY 4.0；保留原整数Wh数值。
- bidmc01：BIDMC PPG and Respiration Dataset v1.0.0。https://physionet.org/content/bidmc/1.0.0/ 。请引用Pimentel等人的数据集及其原论文（数据集页给出正式引用），ODC Attribution License v1.0；采用发布的WFDB format-16数字原值，保留缺失值哨兵，不宣称本项目采集了这些数据。

这里只分发三个小型复现示例。历史完整55记录/新独立记录的统计覆盖范围在evidence中分别说明，三示例的成功不能替代独立数据泛化。完整数据如需补测，应从原始平台按其许可获取并保留源哈希。
