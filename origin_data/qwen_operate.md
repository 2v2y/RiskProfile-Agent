# Qwen3.8-27B 服务器重连与对话操作手册

适用环境：RTX 4090 24GB · Conda(test) · vLLM · BitsAndBytes 4bit

当前已验证：Qwen3.8-27B 可在 RTX 4090 24GB 上以 BitsAndBytes 4bit 成功启动；模型显存约 17.9 GiB，vLLM API 监听 8000 端口。

## 0. SSH 登录

打开 cmd，然后输入：

```bash
ssh root@172.16.201.138
```

然后要求输入密码：

```text
Cnksi.com.2026
```

进入环境：

```bash
conda activate test
```

## 一、服务器重启后：启动模型

SSH 登录服务器后，按以下顺序执行。

### 1. 进入 Conda 环境

```bash
conda activate test
```

### 2. 设置运行环境变量

```bash
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export VLLM_USE_FLASHINFER_SAMPLER=0
```

### 3. 启动 Qwen3.8-27B

```bash
vllm serve /DATA/models/Qwen3.8-27B \
  --quantization bitsandbytes \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192 \
  --language-model-only \
  --enforce-eager \
  --host 0.0.0.0 \
  --port 8000 \
  2>&1 | tee /tmp/qwen38.log
```

启动成功标志：日志最后出现 “Application startup complete.” 和 “API server: HTTP server started”。

说明：这是服务器进程。启动成功后终端会一直停在那里等待请求，这是正常状态，不是卡死。

## 二、推荐方式：使用 screen 后台运行

这样即使 SSH 断开，vLLM 仍可继续运行。

### 1. 创建 screen 会话

```bash
screen -S qwen
```

### 2. 在 screen 内启动模型

```bash
conda activate test
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export VLLM_USE_FLASHINFER_SAMPLER=0
vllm serve /DATA/models/Qwen3.8-27B \
  --quantization bitsandbytes \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192 \
  --language-model-only \
  --enforce-eager \
  --host 0.0.0.0 \
  --port 8000
```

### 3. 模型留在后台

模型启动成功后按：

```text
Ctrl + A
然后按 D
```

这是 detach（离开 screen，但不停止程序）。不要按 Ctrl+C；Ctrl+C 会终止 vLLM。

### 4. 下次重新 SSH 登录后

```bash
screen -ls
```

如果看到 qwen 会话处于 Detached 状态，说明模型仍在后台运行。重新进入：

```bash
screen -r qwen
```

## 三、检查模型服务是否还在运行

每次重新登录后，先执行下面命令。若能返回模型信息，就无需重新启动模型。

```bash
curl http://127.0.0.1:8000/v1/models
```

判断规则：有 JSON 返回 = 服务还活着；连接失败 = 需要重新启动 vLLM。

## 四、直接与模型对话

在另一个 SSH 终端执行：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "/DATA/models/Qwen3.8-27B",
    "messages": [
      {
        "role": "user",
        "content": "你好，请介绍一下你自己。"
      }
    ],
    "temperature": 0.7,
    "max_tokens": 512
  }'
```

## 五、建议制作一键启动脚本

只需配置一次，以后不用手动输入整套启动命令。

```bash
nano /root/start_qwen.sh
```

写入以下内容：

```bash
#!/bin/bash
source /root/miniconda3/etc/profile.d/conda.sh
conda activate test
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export VLLM_USE_FLASHINFER_SAMPLER=0
vllm serve /DATA/models/Qwen3.8-27B \
  --quantization bitsandbytes \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192 \
  --language-model-only \
  --enforce-eager \
  --host 0.0.0.0 \
  --port 8000
```

保存后赋予执行权限：

```bash
chmod +x /root/start_qwen.sh
```

以后服务器重启后，只需要：

```bash
screen -S qwen
/root/start_qwen.sh
```

等待看到服务启动成功，再按 Ctrl+A，然后按 D，将模型留在后台。

## 六、以后最简操作流程

| 场景 | 操作 |
| --- | --- |
| 重新登录，但服务器没重启 | 先 `curl 127.0.0.1:8000/v1/models`；有返回就直接对话。 |
| 模型在 screen 后台 | `screen -ls` 查看；需要看日志时 `screen -r qwen`。 |
| 服务器重启过 | `screen -S qwen` → `/root/start_qwen.sh` → 等启动完成 → Ctrl+A、D。 |
| 想停止模型 | 进入 screen 后按 Ctrl+C。 |
| 想发起对话 | 调用 `http://127.0.0.1:8000/v1/chat/completions`。 |

## 七、当前配置注意事项

- 当前使用 BitsAndBytes 动态 4bit：每次冷启动都要重新读取原始模型并量化加载，约需数分钟。
- 不要为了日志中的可选加速模块 Warning 随意重装 CUDA、PyTorch 或 vLLM；当前环境已经成功跑通。
- 当前采用 `--language-model-only`，因此该服务按纯文本模型运行，不启用图片/视频输入。
- 若以后要长期稳定运行，建议进一步制作永久 4bit checkpoint 或 systemd 服务。
