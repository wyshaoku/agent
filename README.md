# 本地网站开发 Agent

这是一个使用 Ollama 本地模型的命令行网站开发助手。它可以在指定项目目录内查看和修改文件，并启动常见的前端开发命令。

## 准备

1. 启动 Ollama，并确认 `ollama list` 中能看到 `gemma-local`。
2. 安装 Python 3.10 或更新版本。
3. 启动网站开发 Agent：

```powershell
python D:\ai\agent\agent.py --workspace D:\你的项目目录 --model gemma-local
```

模型名默认是 `gemma-local`，也可以省略 `--model`。默认连接 `http://localhost:11434`，可通过 `OLLAMA_HOST` 修改服务地址。启动时只检查 Ollama 和模型是否可用；每次模型请求默认最多等待 900 秒，可用 `--timeout` 调整。Agent 按需执行文件操作和 npm 命令，并显示执行结果。执行 `npm install` 时由 npm 自己校验锁文件并核对已安装依赖；不会仅凭文件时间戳跳过安装，也不会主动升级依赖。用户明确要求升级或修复依赖时，Agent 可以提出必要的 npm 命令，白名单之外的命令会先询问确认。`Modelfile` 仅用于需要重新导入 GGUF 的情况。

## 使用示例

```text
> 帮我做一个简洁的个人作品集网站，使用 React 和 Vite，适配手机端。
> 在首页增加项目卡片和联系我区域。
> /exit
```

输入 `/new` 可开始新对话；Agent 会保留当前进程内的对话上下文，以便继续回答澄清问题。`/mode ask` 切换到只讨论和规划的提问模式，`/mode agent` 恢复执行模式；切换不清除历史。Agent 执行期间按 `Ctrl+C` 请求暂停；当前模型请求会完成后丢弃结果，命令执行则在当前命令结束后暂停。暂停提示中输入新方向后继续，`/resume` 继续原任务，`/restart` 用新方向重开当前任务。

会话会自动保存到 `D:\ai\agent\conversations`。重启后自动恢复最近会话；用 `/history` 查看历史，用 `/resume <编号>` 恢复指定会话，用 `/new` 保存当前会话并新开一个。完整历史保存在本地；每次 Ollama 请求只发送最近 12 条消息及初始任务摘要。动作 JSON 格式最多重试两次。运行决策记录另存为 `conversations/logs/<会话编号>.jsonl`，包括模型原始输出、计划、行动理由、工具结果、格式错误、模式切换和暂停期间的新指令。日志是模型提供的简短理由说明，不代表其内部隐式思维过程。工具失败会触发证据检查、根因诊断、最小修复和原步骤回归验证；未修复前会拦截无状态变化的相同失败操作。只有回归验证成功的经验才会写入 `conversations/experiences.json`，后续 Agent 任务会收到这些经验作为参考。

Agent 的文件读写限制在 `--workspace` 指定的目录，包括 `package.json` 等项目配置文件。创建目录使用 `create_directory`；写文件使用 `write_file`，它会为缺失的父目录自动建目录。父路径若是文件，会报告冲突；可先用 `read_file` 检查，再用不会覆盖目标的 `move_file` 保留移动。`npm init -y`、`npm install`、`npm audit`、`npm audit fix`、`npm run dev`、`npm run build` 和 `npm run lint` 可直接运行；其他命令会先显示完整命令并询问确认。开发服务器类命令在获批后于后台启动，终端会显示访问地址。

## 项目状态与命令结果记忆

Agent 会在每轮开始及每次工具操作后扫描项目关键状态，并把命令结果与实际文件重新对照。项目状态包括检测到的技术栈、package.json 脚本和依赖、lockfile 与清单是否匹配、HTML/应用入口、直接依赖目录、配置缺项及最近命令结果；npm 安装成功会绑定当时的清单指纹，清单后来变化时会标成过期。状态和最近 10 条操作摘要会持久化到 conversations/project_states/，并作为后续决策上下文。

npm init -y 被识别为只生成通用清单；npm install 成功但未声明依赖时，不会被解释成框架已安装；npm audit 报告漏洞也不会被当成安装失败。运行日志会记录项目状态更新与 Agent 给出的简短行动理由，供定位规则问题；这不是模型的隐式思维过程记录。

## 模型兼容性
Agent 不依赖 Ollama 的原生工具调用能力，而是要求模型按 JSON 格式规划任务并提出文件或命令操作，再由 Agent 在工作区内执行。这适用于没有声明 `tools` 能力的 `gemma-local`。如果模型无法持续按要求返回 JSON，Agent 会在重试后提示格式问题。文件访问限制在工作区；白名单之外的命令需经用户确认后执行。

运行恢复流程的本地回归用例：

```powershell
python -m unittest -v test_agent
```
