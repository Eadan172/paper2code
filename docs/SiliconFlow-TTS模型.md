# SiliconFlow TTS（CosyVoice2）

播客语音在配置了 TTS API Key 时优先走 SiliconFlow 的 OpenAI 兼容接口，听感最接近真人。无 Key 或调用失败时，仍按 **edge-tts → Windows SAPI → 静音占位** 回退，原因写进 `warnings`。

## 接口

```http
POST https://api.siliconflow.cn/v1/audio/speech
Authorization: Bearer <tts_api_key>
Content-Type: application/json
```

```json
{
  "model": "FunAudioLLM/CosyVoice2-0.5B",
  "input": "要朗读的文本",
  "voice": "FunAudioLLM/CosyVoice2-0.5B:alex",
  "response_format": "mp3"
}
```

默认 `base_url` 是 `https://api.siliconflow.cn/v1`（不要把 `/audio/speech` 写进 base_url，程序会自己拼上）。长对白会先做口语化，再按句切成约 400 字一块分别请求，最后把 MP3 拼成一条播客。

## 双人音色

CosyVoice2-0.5B 的系统预置音色（请求时必须加模型前缀 `FunAudioLLM/CosyVoice2-0.5B:`）：

| 角色 | 默认 | 说明 |
| --- | --- | --- |
| 主持人 | `alex` | 沉稳男声 |
| 讲解人 | `anna` | 沉稳女声 |

同系列还可以换：男声 `benjamin` / `charles` / `david`，女声 `bella` / `claire` / `diana`。两位说话人要用不同的音色，否则听起来像一个人。

## 怎么配置 Key

三选一，优先级是 **环境变量 > `configs/api_keys.yaml` > 代码默认值**。设置页写入的是进程内存，勾选保存时再落到 gitignore 的 `configs/api_keys.yaml`。

```bash
# 环境变量（不要写进仓库）
export P2C_TTS_API_KEY="你的 SiliconFlow Key"
# 可选
export P2C_TTS_BASE_URL="https://api.siliconflow.cn/v1"
export P2C_TTS_MODEL="FunAudioLLM/CosyVoice2-0.5B"
export P2C_TTS_VOICE="FunAudioLLM/CosyVoice2-0.5B:alex"
export P2C_TTS_VOICE_ALT="FunAudioLLM/CosyVoice2-0.5B:anna"
export P2C_TTS_PROVIDER="auto"   # auto | siliconflow | edge | openai_speech
```

或复制模板后只改本机文件（该文件已被 `.gitignore` 排除）：

```bash
cp configs/api_keys.yaml.example configs/api_keys.yaml
# 编辑 tts_api_key / tts_voice / tts_voice_alt
```

Dashboard 顶部的「语音 · SiliconFlow」也可以粘贴 Key、改两个音色，并测试连接。状态接口只返回末四位提示，不会把 Key 放进响应或日志。

## 引擎顺序

| `tts_mode` / `tts_provider` | 行为 |
| --- | --- |
| `auto` 且已配置 Key | SiliconFlow → edge-tts → SAPI → 静音占位 |
| `auto` 且没有 Key | edge-tts → SAPI → 静音占位 |
| `siliconflow` | 先试云端；无 Key 或 HTTP 失败再走本地链，并记下原因 |
| `edge` / `sapi` | 只用该本地后端，不用云端 |
| `off` | 只出字幕和脚本 |

`tts_provider=edge` 同样强制本地链路，即使机器上已经配了 Key。
