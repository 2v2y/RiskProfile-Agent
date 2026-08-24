# 《电力相关单位动态违章画像与排序方法》程序运行说明

## 1. 研究流水线边界

本目录是正式分析程序，不生成或填写研究结果。`fixtures/`仅供程序结构自检，不得作为论文证据。

本项目使用程序性隔离而非密码学盲测。OSHA原始公开数据理论上可以重建Test标签，因此依靠预注册、文件哈希和角色隔离控制选择偏倚：原始数据、`inspection_clean.csv`、`violation_clean.csv`、`historical_inspection_outcomes.csv`、`inspection_episode.csv`、Test特征与标签仅由学生1（数据保管人）在受限环境访问；学生1不参与模型或规则选择；学生3只接收Train/Validation画像结果，学生4、5只核对Test承诺文件和最终无标签预测。03不读Test特征，03b由学生1在模型冻结后受限运行。受限目录在POSIX正式环境强制0700、文件0600；同一Unix账号仍须用不同账户/ACL或三个独立环境落实角色隔离，权限控制不是加密。

## 2. 安装

在执行包根目录建立虚拟环境后运行：

```bash
python3 -m pip install -r 程序/requirements.txt
```

## 3. 下载官方数据

API密钥只通过临时环境变量提供，脚本不会把密钥写入URL、文件或日志。

macOS/zsh：

```bash
export DOL_API_KEY='从DOL门户取得的密钥'
python3 程序/00_download_official_data.py
unset DOL_API_KEY
```

Windows PowerShell：

```powershell
$env:DOL_API_KEY='从DOL门户取得的密钥'
python 程序/00_download_official_data.py
Remove-Item Env:DOL_API_KEY
```

只核对官方目录metadata（无需密钥）：

```bash
python3 程序/00_download_official_data.py --metadata-only
```

程序按实际返回行数推进offset；短页不作为终点，只有0行页表示结束。`--max-pages 1`只作端点核验，不生成“完整”结论。2010—2014为历史预热期，候选样本仍从2015Q1开始。

## 4. 正式阶段与人工门

```bash
python3 程序/run_pipeline.py
```

`run_pipeline.py`仅用于导师/审计复现已经完成全部人工门的01、02、03和开封前05，首次正式运行应按角色逐阶段执行。01会核验下载manifest、路径、行数、请求参数和SHA-256，以`downloaded_at_utc`冻结as-of，并输出成熟度审计；02会核验双人签字的画像定义冻结表，并从实体复核CSV独立重算PPV与Wilson下界。03只核对并冻结预先固定的方法（没有Validation调参或模型选择），不读取Test特征或标签，也不生成Test预测。

模型冻结后，学生1在受限环境执行一次：

```bash
python3 程序/03b_generate_sealed_predictions.py
```

03b核验Test特征commitment、冻结清单和模型后生成`test_predictions_sealed.csv`与`test_prediction_commitment.json`，已有任一文件即拒绝覆盖。任何阶段发现`test_open_attempt.json`或`test_open_record.json`后，00/01/02/03/03b/run_pipeline均停止写入。

## 5. Test唯一开封入口

冻结配置、模型、两个Test commitment、预测和门0—门3复核签字全部完成后，只执行一次：

```bash
python3 程序/04_run_sealed_test.py --confirm-open-test
python3 程序/05_make_paper_tables.py
```

除固定的`README_正式测试结果说明.md`外，只要正式Test结果目录已有文件，程序就拒绝覆盖。不得为了改善结果删除记录后重跑。

04会在首次读取Test标签前，将`记录表/测试开封记录.csv`最后一条“批准开封”记录固化为`结果/04_正式测试_封存/test_open_approval_snapshot.json`。开封证据链锚定该不可变快照；开封结束后可把原CSV状态更新为“已完成”，不会与05制表校验冲突，但快照、attempt和open record均不得修改或删除。

## 6. 离线自检

```bash
python3 程序/smoke_test.py
```

自检不联网、不读取正式数据；它只在临时目录中开封标明为fixture的合成Test，绝不打开正式Test。自检还会把fixture开封CSV从“批准开封”更新为“已完成”，再验证05仍可依据不可变snapshot制表。成功标志：`SMOKE TEST PASS`。

正式规模性能复核：

```bash
python3 程序/performance_test.py
python3 程序/performance_test.py --entities 10000 --history-per-entity 100 --episodes-per-entity 4 --max-seconds 60 --max-rss-mb 2048
```

01会分块扫描原始CSV，以目标NAICS和关联activity收敛数据，并用SQLite做磁盘去重；02按实体排序后用前缀量和Fenwick树计算时间窗。验收结果见`程序/规模性能验收.md`。

## 7. 主要固定输出

- 01：受限中间表与Test标签；`成熟度审计.csv`、`图2_样本筛选流程.svg`和Test标签commitment（只披露候选/成熟总数，不披露Test阳性数）。episode内每个Inspection用自身`[open, open+180天]`；标签为OR，阳性按最早citation+30天可用，只有OR阴性才等待全部组成检查关闭并成熟。
- 02：`profiles_train_val.csv`、完整`feature_dictionary.csv`、防泄漏审计、匿名复算证据卡、图1/图3；受限Test特征及`test_features_commitment.json`。
- 03：Validation指标、`frozen_model.joblib`、`frozen_config.json`、`environment_versions.json`和`frozen_manifest.json`；不产Test预测。
- 03b：`结果/03_验证/test_predictions_sealed.csv`和`test_prediction_commitment.json`。
- 04：季度指标、汇总、Bootstrap置信区间、校准分箱、Test每季成熟率、`test_open_attempt.json`、批准行snapshot和open record。
- 05：表1—表4、图1—图4、可定位到源行列与SHA-256的长表数字索引及来源清单。表4含M2−M1差值、95%区间、C1/C2和有效Bootstrap次数。

AP使用标准score-threshold定义（相同score作为同一阈值组，支持sample weight）；`sample_id`只用于Recall@20%容量边界的确定性顺序，不影响AP。

字段缺失、Test季度不全、Train/Validation标签不成熟、实体复核未过门或冻结哈希不一致时，程序会停止并给出错误；不会猜字段、补假数据或绕过门禁。
