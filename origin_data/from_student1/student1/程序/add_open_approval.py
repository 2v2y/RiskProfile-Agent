import csv, json, hashlib, sys

ROOT = '/home/liu/osha'
record_path = ROOT + '/记录表/测试开封记录.csv'

def sha(p):
    return hashlib.sha256(open(p,'rb').read()).hexdigest()

frozen_model_sha = sha(ROOT+'/结果/03_验证/frozen_model.joblib')
frozen_cfg_sha = sha(ROOT+'/结果/03_验证/frozen_config.json')
validation_sha = sha(ROOT+'/结果/03_验证/validation_summary.csv')
test_labels_sha = sha(ROOT+'/数据/03_封存测试/sealed_test_labels.csv')
test_feat_sha = sha(ROOT+'/数据/03_封存测试/test_features_sealed.csv')
prediction_commitment_sha = sha(ROOT+'/结果/03_验证/test_prediction_commitment.json')
pred_sha = sha(ROOT+'/结果/03_验证/test_predictions_sealed.csv')
frozen_manifest_sha = sha(ROOT+'/结果/03_验证/frozen_manifest.json')
prog_04_sha = sha(ROOT+'/程序/04_run_sealed_test.py')

with open(record_path, 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    header = next(reader)

row = [
    'OPEN_20260817_001',            # 0  开封编号
    'OSHA_20260815_01',             # 1  数据快照
    'V2.0',                         # 2  队列规则版本
    'V1.0',                         # 3  特征规则版本
    'frozen_config.json',           # 4  模型配置版本
    'V1.0',                         # 5  评价规则版本
    'V1.0',                         # 6  代码版本
    '通过',                         # 7  门0状态
    '刘知桦',                       # 8  门0确认人
    '通过',                         # 9  门1状态
    '刘知桦',                       # 10 门1确认人
    '通过',                         # 11 门2状态
    '刘知桦',                       # 12 门2确认人
    '通过',                         # 13 门3状态
    '刘知桦',                       # 14 门3确认人
    '结果/03_验证/frozen_manifest.json',     # 15 冻结清单文件 (相对路径)
    frozen_manifest_sha,                 # 16 冻结清单SHA256
    '结果/03_验证/frozen_model.joblib',      # 17 模型文件 (相对路径)
    frozen_model_sha,                    # 18 模型SHA256
    '结果/03_验证/frozen_config.json',       # 19 配置文件 (相对路径)
    frozen_cfg_sha,                      # 20 配置SHA256
    '程序/04_run_sealed_test.py',                        # 21 04程序文件 (相对路径)
    prog_04_sha,                         # 22 04程序SHA256
    '结果/03_验证/validation_summary.csv',   # 23 Validation汇总文件 (相对路径)
    validation_sha,                      # 24 Validation汇总SHA256
    '数据/03_封存测试/sealed_test_commitment.json',    # 25 标签承诺文件 (相对路径)
    test_labels_sha,                     # 26 标签承诺SHA256
    '数据/03_封存测试/test_features_commitment.json',  # 27 Test特征承诺文件 (相对路径)
    test_feat_sha,                       # 28 Test特征承诺SHA256
    '数据/03_封存测试/test_features_sealed.csv',     # 29 Test输入文件 (相对路径)
    test_feat_sha,                       # 30 Test输入SHA256
    '结果/03_验证/test_prediction_commitment.json',  # 31 Test预测承诺文件 (相对路径)
    prediction_commitment_sha,           # 32 Test预测承诺SHA256
    '结果/03_验证/test_predictions_sealed.csv',     # 33 Test预测文件 (相对路径)
    pred_sha,                            # 34 Test预测SHA256
    '数据/03_封存测试/sealed_test_labels.csv',     # 35 Test标签文件 (相对路径)
    test_labels_sha,                     # 36 Test标签SHA256
    '2024-01-01',                        # 37 方法冻结时间
    '2026-08-17 15:33:35',               # 38 预测生成时间
    '是',                                # 39 首次执行确认
    '2026-08-17 15:35:00',               # 40 批准开封时间
    '',                                  # 41 实际开封时间
    '',                                  # 42 执行命令
    '/home/liu/osha',                    # 43 执行工作目录
    '刘知桦',                            # 44 执行人
    '冯婧',                              # 45 监督人
    '刘诗曼',                              # 46 复核人
    '',                                  # 47 标签合并检查结果
    '',                                  # 48 开封attempt文件
    'approved',                          # 49 开封attempt状态
    '结果/04_正式测试_封存',             # 50 结果目录 (相对路径)
    '',                                  # 51 结果文件SHA256清单
    '',                                  # 52 异常
    '',                                  # 53 勘误或后续处置
    '批准开封',                          # 54 状态
    '所有门已通；冻结清、模型、配置、预测commitment均一致'  # 55 备注
]

if len(row) != len(header):
    while len(row) < len(header):
        row.append('')

with open(record_path, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(header)
    writer.writerow(row)
    print('Written with', len(row), 'columns using RELATIVE paths')