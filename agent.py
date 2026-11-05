#!/usr/bin/env python3
"""A small, workspace-confined website-building agent for Ollama."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = """你是一名网站开发 Agent，通过用户电脑上的本地程序开发网站。
理解需求后直接处理；复杂任务可简要列计划，不要为了计划额外调用一次模型。遇到非关键偏好或可逆的小选择时，采用常见合理默认值并继续，不要停下来征求意见。只有缺少继续任务必需的信息、且无法从工作区或上下文推断时，才提出一个简短问题；不要仅因回复中有问句就结束任务。按需读取和修改文件，完成后反馈真实结果。简单问答可以直接回复。工具或命令失败后必须进入恢复流程：检查完整错误输出和相关文件/配置，说明有证据的原因，做最小修复，再重新执行原失败步骤进行回归验证；验证未成功前不得声称解决。不能把工具协议错误说成系统没有文件访问能力；list_files 用于目录，read_file 用于文件。create_directory 用于创建目录，write_file 用于创建或整体写入文件；modify_file 用于按 old_text/new_text 精确修改已有文件，默认要求 old_text 只出现一次；不要把 mkdir 命令放进文件内容。文件操作和命令默认相对当前工作目录；需要时用 change_directory 在项目工作区内切换，路径不得超出工作区。静态 HTML 不需要 npm run dev；启动前先确认 package.json 有 scripts.dev。
每次回复都必须是一个 JSON 对象，不得使用 Markdown 代码围栏或 JSON 以外的文字。一次回复只能包含一个 JSON 对象；不要连续输出多个对象。每轮只执行一个动作。收到命令结果后，必须阅读结果和随后提供的“项目状态”，根据工作区实际文件结构、用户目标和操作结果继续决策；不要把某一种项目的文件清单当成所有项目都必须遵循的结构。若“失败检测”为“是”，先处理并重试“失败记录”中的动作；失败记录在检测为“是”期间保持不变，只有该动作成功重试后检测才会恢复为“否”。npm init -y 只创建通用 package.json，不会自动添加 Vite、scripts.dev 或应用源码；npm install 成功只说明 npm 按当前清单完成处理，若没有声明依赖，不得说框架依赖已安装；“audited N packages”是审计数量，不是配置完整或安装了 N 个应用依赖的证明。下一步动作的 rationale 要简短说明使用了哪些证据以及为什么采取该动作；如果现有证据不足，先检查文件或配置。不要根据单一命令的成功退出码推断项目已经可运行。
开发任务先使用格式：
{"action":"plan","steps":["查看现有项目","实现需求","运行构建并反馈结果"]}
计划后每轮只提出一个工具操作，使用格式：
{"action":"list_files","args":{"path":"."}}
可用 action：list_files（args.path 可选）、read_file（args.path）、write_file（args.path 和 args.content）、modify_file（args.path、args.old_text、args.new_text，可选 replace_all）、set_project_goal（args.goal）、create_directory（args.path）、move_file（args.source 和 args.destination，仅移动文件且拒绝覆盖）、change_directory（args.path，切换项目工作区内的当前目录）、run_command（args.command）。路径默认相对当前工作目录；绝对路径也必须位于项目工作区中。所有 npm 命令都必须用 action=run_command，并把完整命令放在 args.command；目录操作必须调用 create_directory；move_file 用于在工作区内无覆盖地移动已有文件。默认可直接执行 npm init -y、npm install、npm install <包>@<版本>、npm audit、npm audit fix、npm run dev、npm run build、npm run lint、npm test、npm run test、npm run check、npm run typecheck、npm run preview、npm run serve、npm run watch、npm start、npm run start。其他命令可以提出，但 Agent 会先向用户展示完整命令并询问；只有用户明确同意后才会执行。不得声称未执行的命令已经运行。npm audit 只用于查看漏洞；漏洞数量本身不是安装失败，不要主动升级主版本。
完成当前请求、回答问题或需要向用户提问时，使用格式：{"action":"reply","content":"给用户的中文回复"}。reply 是标准回复动作；replay、respond、response、answer、final 等名称也会兼容为 reply。
计划中的步骤请附带简短理由；工具动作可附带 rationale 字段说明为什么做；不采取行动或跳过步骤时，也请用 reply 或 plan 简述原因。
先理解需求，优先沿用项目已有技术栈；空目录时可使用 Vite。空工作区根据用户目标创建必要文件，不套用固定文件清单；只有所选工具链要求时才创建对应配置和入口。Vue 单文件组件要有匹配的 Vite 插件及 main.js 挂载入口。写源码时使用真实换行，不要把字面量 `\n` 写进 CSS/HTML。只包含 index.html 的静态网站不需要 npm。仅当 package.json/lockfile 不存在或依赖有变化时才运行 npm install；不要主动升级依赖。用户明确要求升级或修复依赖时，可以提出 npm update、npm install <包名>@<版本> 等必要命令；未列入白名单的命令仍先询问用户，获批后再执行。运行 Vite 时如报错再修复，不要在启动前反复检查无关配置。命令失败后根据错误尝试修复；任务完成后根据真实结果反馈。启动网站时报告实际输出的网址，不要猜测端口。不要声称做了实际上未执行的操作。所有文件操作都限制在工作区内。"""


def path_in_workspace(workspace: Path, raw: str, current_directory: Path | None = None) -> Path:
    base = current_directory or workspace
    candidate = (base / raw).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise ValueError("路径超出工作区范围")
    return candidate


def dependency_signature(workspace: Path) -> str:
    digest = hashlib.sha256()
    for name in ("package.json", "package-lock.json"):
        file = workspace / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes() if file.is_file() else b"<missing>")
    return digest.hexdigest()


TOOL_ACTIONS = {"list_files", "read_file", "write_file", "modify_file", "set_project_goal", "create_directory", "move_file", "change_directory", "run_command"}
PROJECT_TYPE_DETECTION_ENABLED = False
PROJECT_STATE_CONTEXT_CHARS = 1600


def _restore_css_linebreak_escapes(text: str) -> tuple[str, int]:
    out: list[str] = []
    quote: str | None = None
    in_comment = False
    escaped = False
    changes = 0
    i = 0
    while i < len(text):
        char = text[i]
        next_char = text[i + 1] if i + 1 < len(text) else ""
        if in_comment:
            out.append(char)
            if char == "*" and next_char == "/":
                out.append(next_char)
                i += 2
                in_comment = False
                continue
            i += 1
            continue
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            i += 1
            continue
        if char == "/" and next_char == "*":
            out.extend((char, next_char))
            i += 2
            in_comment = True
            continue
        if char in {"'", '"'}:
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "\\" and next_char in {"n", "r", "t"}:
            if next_char == "r" and i + 3 < len(text) and text[i + 2:i + 4] == "\\n":
                i += 4
            else:
                i += 2
            out.append("\n" if next_char != "t" else "\t")
            changes += 1
            continue
        out.append(char)
        i += 1
    return "".join(out), changes


def normalize_write_content(path: Path, content: str) -> tuple[str, int]:
    """Repair model-escaped source line breaks without touching quoted CSS strings."""
    suffix = path.suffix.lower()
    changes = 0
    if suffix in {".css", ".scss", ".less"}:
        content, changes = _restore_css_linebreak_escapes(content)
    elif suffix == ".vue":
        pattern = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.IGNORECASE | re.DOTALL)

        def fix_style(match: re.Match[str]) -> str:
            nonlocal changes
            style, count = _restore_css_linebreak_escapes(match.group(2))
            changes += count
            return match.group(1) + style + match.group(3)

        content = pattern.sub(fix_style, content)

    # If the entire source file arrived on one line with escaped separators,
    # it was double-escaped as a whole. Avoid this fallback for JSON/data files.
    source_exts = {".vue", ".css", ".scss", ".less", ".html", ".htm", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
    if suffix in source_exts and "\n" not in content and content.count("\\n") >= 3:
        content = content.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
        changes += content.count("\n")
    return content, changes


def start_background_command(workspace: Path, command: str, current_directory: Path | None = None) -> str:
    proc = subprocess.Popen(
        command if os.name == "nt" else shlex.split(command),
        cwd=current_directory or workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=os.name == "nt",
    )
    lines: list[str] = []
    output_queue: queue.Queue[str | None] = queue.Queue()
    output_closed = False
    if proc.stdout:
        def collect_output() -> None:
            for line in proc.stdout:
                output_queue.put(line.rstrip())
            output_queue.put(None)

        threading.Thread(target=collect_output, daemon=True).start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                line = output_queue.get(timeout=0.25)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if line is None:
                output_closed = True
                break
            lines.append(line)
            if "localhost:" in line or "127.0.0.1:" in line:
                break
    if proc.poll() is not None:
        return f"后台命令启动失败，退出码：{proc.returncode}\n" + "\n".join(lines)
    collected = "\n".join(lines)
    if output_closed:
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        if proc.poll() is not None:
            return f"后台命令启动失败，退出码：{proc.returncode}\n{collected}"
    if re.search(r"npm (?:error|ERR!) Missing script:", collected, re.IGNORECASE):
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return f"后台命令启动失败，退出码：{proc.returncode if proc.poll() is not None else '未知'}\n{collected}"
    details = "\n".join(lines) or "命令仍在运行。"
    return f"后台进程已启动，PID={proc.pid}\n{details}"


def is_clarification(text: str) -> bool:
    # Optional questions are not blockers. Stop only when essential information
    # is explicitly described as necessary to continue.
    return bool(re.search(
        r"(?:缺少|尚未提供|无法确定).{0,40}(?:无法继续|不能继续|无法执行|不能执行|无法完成|不能完成)"
        r"|(?:请提供|请补充|需要你提供).{0,50}(?:才能继续|否则无法继续|否则不能继续|才能执行|才能完成)",
        text,
    ))


def prepare_vite_vue2_project(workspace: Path) -> tuple[bool, str]:
    """Repair the common Vue 2.7 + Vite scaffold when its entry files are missing."""
    package_file = workspace / "package.json"
    try:
        package = json.loads(package_file.read_text(encoding="utf-8"))
    except Exception:
        return False, ""
    app_file = workspace / "src" / "App.vue"
    if not app_file.is_file():
        return False, ""

    changed: list[str] = []
    src_dir = workspace / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    if not (src_dir / "main.js").exists():
        (src_dir / "main.js").write_text(
            "import Vue from 'vue'\nimport App from './App.vue'\n\nnew Vue({ render: (h) => h(App) }).$mount('#app')\n",
            encoding="utf-8",
            newline="\n",
        )
        changed.append("创建 src/main.js")

    index_file = workspace / "index.html"
    if index_file.exists():
        html = index_file.read_text(encoding="utf-8")
        if "src/main.js" not in html:
            script = '<script type="module" src="/src/main.js"></script>'
            html = html.replace("</body>", f"  {script}\n</body>") if "</body>" in html else html + "\n" + script + "\n"
            index_file.write_text(html, encoding="utf-8", newline="\n")
            changed.append("将 src/main.js 接入项目根入口")
    else:
        old_index = src_dir / "index.html"
        if old_index.exists():
            html = old_index.read_text(encoding="utf-8")
        else:
            html = '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>网站</title></head><body><div id="app"></div></body></html>'
        script = '<script type="module" src="/src/main.js"></script>'
        if "src/main.js" not in html:
            html = html.replace("</body>", f"  {script}\n</body>") if "</body>" in html else html + "\n" + script + "\n"
        index_file.write_text(html, encoding="utf-8", newline="\n")
        changed.append("创建项目根目录 index.html 并连接 src/main.js")

    dev_deps = package.setdefault("devDependencies", {})
    if "@vitejs/plugin-vue2" not in dev_deps:
        dev_deps["@vitejs/plugin-vue2"] = "^2.3.4"
        changed.append("添加 @vitejs/plugin-vue2")
    config_file = workspace / "vite.config.mjs"
    if not config_file.exists():
        config_file.write_text(
            "import { defineConfig } from 'vite'\nimport vue from '@vitejs/plugin-vue2'\n\nexport default defineConfig({ plugins: [vue()] })\n",
            encoding="utf-8",
            newline="\n",
        )
        changed.append("创建 vite.config.mjs")
    if changed:
        package_file.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, "；".join(changed)
    return False, ""


def existing_file_ancestor(workspace: Path, path: Path) -> Path | None:
    """Find a parent component that is already a file, not a directory."""
    current = path
    while current != workspace and current != current.parent:
        if current.exists() and not current.is_dir():
            return current
        current = current.parent
    return None


def run_tool(workspace: Path, name: str, args: dict[str, Any], current_directory: Path | None = None) -> str:
    try:
        if name not in TOOL_ACTIONS:
            return f"未知工具：{name}"
        if not isinstance(args, dict):
            return f"工具参数格式错误：{name} 的 args 必须是 JSON 对象。"
        if name == "list_files":
            folder = path_in_workspace(workspace, args.get("path", "."), current_directory)
            entries = []
            for item in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                if item.name in {"node_modules", ".git", ".venv", "__pycache__"}:
                    continue
                entries.append(("目录 " if item.is_dir() else "文件 ") + item.relative_to(workspace).as_posix())
            return "\n".join(entries) or "（空目录）"
        if name == "read_file":
            if not isinstance(args.get("path"), str) or not args["path"].strip():
                return "工具参数缺失：read_file 需要非空字符串 path。"
            file = path_in_workspace(workspace, args["path"], current_directory)
            content = file.read_text(encoding="utf-8")
            return content if len(content) <= 30000 else content[:30000] + "\n…（内容已截断）"
        if name == "write_file":
            if not isinstance(args.get("path"), str) or not args["path"].strip():
                return "工具参数缺失：write_file 需要非空字符串 path。"
            if not isinstance(args.get("content"), str):
                return "工具参数缺失：write_file 需要字符串 content。"
            file = path_in_workspace(workspace, args["path"], current_directory)
            directory_names = {"src", "public", "assets", "components", "pages", "tests", "test", "styles", "images", "fonts", "scripts"}
            if not file.suffix and file.name.casefold() in directory_names:
                return f"路径意图冲突：`{args['path']}` 看起来是目录路径。write_file 只写文件；请调用 create_directory 创建目录，再写入目录中的具体文件。"
            if not file.suffix and re.search(r"\bmkdir\b|CreateDirectory\s*\(", args["content"], re.IGNORECASE):
                return "工具意图冲突：write_file 会把 content 当文件文本保存，不会执行 mkdir。请用 create_directory 创建目录。"
            blocker = existing_file_ancestor(workspace, file.parent)
            if blocker:
                relative = blocker.relative_to(workspace).as_posix()
                return f"路径冲突：父路径 `{relative}` 已是文件，不能作为目录写入子文件。请先用 read_file 检查；需要目录时用 move_file 保留移动该文件，再用 create_directory 创建目录。"
            if file.exists() and file.is_dir():
                return f"路径冲突：`{args['path']}` 已是目录，write_file 需要文件路径。"
            file.parent.mkdir(parents=True, exist_ok=True)
            content, repairs = normalize_write_content(file, args["content"])
            file.write_text(content, encoding="utf-8", newline="\n")
            suffix = f"；自动还原了 {repairs} 处源码换行符" if repairs else ""
            return f"已写入：{file}（{len(content)} 个字符{suffix}）"
        if name == "modify_file":
            if not isinstance(args.get("path"), str) or not args["path"].strip():
                return "工具参数缺失：modify_file 需要非空字符串 path。"
            old_text, new_text = args.get("old_text"), args.get("new_text")
            if not isinstance(old_text, str) or not old_text:
                return "工具参数缺失：modify_file 需要非空字符串 old_text。"
            if not isinstance(new_text, str):
                return "工具参数缺失：modify_file 需要字符串 new_text。"
            replace_all = args.get("replace_all", False)
            if not isinstance(replace_all, bool):
                return "工具参数格式错误：modify_file 的 replace_all 必须是布尔值。"
            file = path_in_workspace(workspace, args["path"], current_directory)
            if not file.exists() or not file.is_file():
                return f"修改失败：文件不存在或不是普通文件：{file}"
            content = file.read_text(encoding="utf-8")
            occurrences = content.count(old_text)
            if not occurrences:
                return f"修改失败：在 {file} 中找不到 old_text，文件未更改。"
            if occurrences > 1 and not replace_all:
                return f"修改失败：old_text 在文件中出现 {occurrences} 次；请提供更精确的片段，或明确设置 replace_all=true。文件未更改。"
            updated = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
            updated, repairs = normalize_write_content(file, updated)
            file.write_text(updated, encoding="utf-8", newline="\n")
            changed = occurrences if replace_all else 1
            suffix = f"；自动还原了 {repairs} 处源码换行符" if repairs else ""
            return f"已修改：{file}（替换 {changed} 处{suffix}）"
        if name == "set_project_goal":
            goal = args.get("goal")
            if not isinstance(goal, str) or not goal.strip():
                return "工具参数缺失：set_project_goal 需要非空字符串 goal。"
            if len(goal) > 2000:
                return "设置失败：项目目标不能超过 2000 个字符。"
            return "项目目标已更新。"
        if name == "create_directory":
            if not isinstance(args.get("path"), str) or not args["path"].strip():
                return "工具参数缺失：create_directory 需要非空字符串 path。"
            directory = path_in_workspace(workspace, args["path"], current_directory)
            if directory.exists():
                if directory.is_dir():
                    return f"目录已存在：{directory}"
                return f"路径冲突：`{args['path']}` 已是文件，无法在同一路径创建目录。请先 read_file 检查，再用 move_file 保留移动文件。"
            blocker = existing_file_ancestor(workspace, directory.parent)
            if blocker:
                relative = blocker.relative_to(workspace).as_posix()
                return f"路径冲突：父路径 `{relative}` 已是文件，不能创建子目录。请先 read_file 检查，再用 move_file 保留移动文件。"
            directory.mkdir(parents=True, exist_ok=True)
            return f"已创建目录：{directory}"
        if name == "move_file":
            source_raw, destination_raw = args.get("source"), args.get("destination")
            if not isinstance(source_raw, str) or not source_raw.strip() or not isinstance(destination_raw, str) or not destination_raw.strip():
                return "工具参数缺失：move_file 需要非空字符串 source 和 destination。"
            source = path_in_workspace(workspace, source_raw, current_directory)
            destination = path_in_workspace(workspace, destination_raw, current_directory)
            if source == destination:
                return "移动已取消：source 和 destination 指向同一路径。"
            if not source.exists() or not source.is_file():
                return "移动失败：source 必须是工作区内已存在的普通文件。"
            if destination.exists():
                return f"移动失败：目标已存在，未覆盖：{destination}"
            blocker = existing_file_ancestor(workspace, destination.parent)
            if blocker:
                relative = blocker.relative_to(workspace).as_posix()
                return f"移动失败：目标父路径 `{relative}` 已是文件。"
            destination.parent.mkdir(parents=True, exist_ok=True)
            created_destination = False
            try:
                with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
                    created_destination = True
                    shutil.copyfileobj(source_stream, destination_stream)
                    destination_stream.flush()
                    os.fsync(destination_stream.fileno())
                shutil.copystat(source, destination)
                source.unlink()
            except Exception:
                if created_destination and source.exists():
                    destination.unlink(missing_ok=True)
                raise
            return f"已移动文件：{source} → {destination}（目标未覆盖）"
        if name == "change_directory":
            if not isinstance(args.get("path"), str) or not args["path"].strip():
                return "工具参数缺失：change_directory 需要非空字符串 path。"
            directory = path_in_workspace(workspace, args["path"], current_directory)
            if not directory.exists():
                return f"切换失败：目录不存在：{directory}。请先 list_files 检查，必要时用 create_directory 创建。"
            if not directory.is_dir():
                return f"切换失败：目标不是目录：{directory}"
            return f"当前工作目录已切换为：{directory}"
        if name == "run_command":
            command = args.get("command")
            if not isinstance(command, str) or not command.strip():
                return "工具参数缺失：run_command 需要非空字符串 command；命令未执行。"
            project_directory = current_directory or workspace
            allowed = {
                "npm init -y", "npm install", "npm audit", "npm audit fix",
                "npm run dev", "npm run build", "npm run lint",
                "npm test", "npm run test", "npm run check", "npm run typecheck",
                "npm run preview", "npm run serve", "npm run watch", "npm start", "npm run start",
            }
            approved_unlisted = False
            if command not in allowed and not is_safe_npm_install_command(command):
                if not isinstance(command, str) or not command.strip() or len(command) > 1000:
                    return "命令为空或过长，未执行。"
                answer = input(f"模型请求执行未列入默认白名单的命令：\n  {command}\n是否在当前项目目录 {project_directory} 执行？[y/N] ").strip().lower()
                if answer not in {"y", "yes"}:
                    return f"用户拒绝执行命令：{command}"
                approved_unlisted = True
            if command == "npm run dev":
                state = _derive_project_snapshot(project_directory, {}) if PROJECT_TYPE_DETECTION_ENABLED else None
                if state and state["project_type"]["value"] == "static_html":
                    return "无需执行：当前工作区有 HTML 入口且未检测到 Vite 配置或依赖，证据指向静态 HTML 项目；它不需要 npm run dev，也不应为此补造 scripts.dev。"
            install_without_manifest = command == "npm install" or is_safe_npm_install_command(command)
            if command != "npm init -y" and not install_without_manifest and not (project_directory / "package.json").is_file():
                return (
                    f"未执行 `{command}`：当前项目目录 {project_directory} 中没有 package.json。"
                    "如果用户要 React/Vite 项目，请先用 write_file 创建 package.json 和所需源码文件，"
                    "再运行 npm install；如果这是纯静态 HTML 页面，则无需 npm 命令。"
                )
            if command == "npm run dev":
                try:
                    package = json.loads((project_directory / "package.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    return f"命令未执行：无法读取有效的 package.json：{type(exc).__name__}: {exc}"
                scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
                if not isinstance(scripts, dict) or not isinstance(scripts.get("dev"), str) or not scripts["dev"].strip():
                    return '命令未执行：package.json 中没有 scripts.dev。先依据项目状态判断这是静态 HTML 还是 Vite；只有确认是 Vite 项目后才补配置。'
            if command == "npm install":
                package_file = project_directory / "package.json"
                lock_file = project_directory / "package-lock.json"
                modules_dir = project_directory / "node_modules"
                # Modification times are only a hint: package.json may be edited
                # while preserving timestamps, and node_modules may be partial.
                # Let npm validate the lockfile and reconcile installed packages.
            is_background = command in {"npm run dev", "npm run preview", "npm run serve", "npm run watch", "npm start", "npm run start"} or (
                approved_unlisted and bool(re.fullmatch(r"npm (?:run (?:preview|serve|watch)|start)(?:\s+.*)?", command))
            )
            if command == "npm run dev":
                fixed, changes = prepare_vite_vue2_project(project_directory)
                if not (project_directory / "index.html").is_file():
                    misplaced = project_directory / "src" / "index.html"
                    hint = f"找到 {misplaced}，但无法自动确认 Vue 入口。" if misplaced.is_file() else "项目中也没有找到 src/index.html。"
                    setup = f"警告：项目根目录缺少 index.html。{hint}仍将尝试启动 Vite，并把真实结果返回给 Agent。\n"
                else:
                    setup = f"已自动补齐 Vue/Vite 入口：{changes}。\n" if fixed else ""
                if fixed:
                    install_result = run_tool(workspace, "run_command", {"command": "npm install"}, project_directory)
                    if not install_result.startswith("退出码：0"):
                        return setup + "依赖安装失败：\n" + install_result
                    setup += "自动依赖安装结果：\n" + install_result + "\n"
            else:
                setup = ""
            if is_background:
                return setup + start_background_command(workspace, command, current_directory)
            result = subprocess.run(
                command if os.name == "nt" else shlex.split(command),
                cwd=current_directory or workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                shell=os.name == "nt",
            )
            output = (result.stdout + "\n" + result.stderr).strip()
            return f"退出码：{result.returncode}\n{output[-12000:]}"
        return f"未知工具：{name}"
    except Exception as exc:
        return f"工具执行失败：{type(exc).__name__}: {exc}"


def _bounded_chat_content(content: str, limit: int = 6000) -> str:
    if len(content) <= limit:
        return content
    marker = "\n…（中间内容已省略）…\n"
    head_size = (limit - len(marker)) // 2
    return content[:head_size] + marker + content[-(limit - len(marker) - head_size):]


MAX_OLLAMA_CONTEXT_CHARS = 3500


def build_ollama_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize stored history into bounded, strictly alternating chat turns."""
    system_text = "\n\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
    chat_messages: list[dict[str, Any]] = []
    for item in messages:
        role = item.get("role")
        if role == "system":
            continue
        if role not in {"user", "assistant"}:
            continue
        current = {"role": role, "content": str(item.get("content", ""))}
        if chat_messages and chat_messages[-1]["role"] == role:
            chat_messages[-1]["content"] += "\n\n" + current["content"]
        else:
            chat_messages.append(current)
    if chat_messages and chat_messages[0]["role"] == "assistant":
        chat_messages.insert(0, {"role": "user", "content": "以下是恢复的历史助手内容；请结合后续用户消息继续处理。"})
    if chat_messages and chat_messages[-1]["role"] == "assistant":
        chat_messages.append({"role": "user", "content": "请继续处理当前任务。"})
    # Keep the full conversation in local history, but send only the initial
    # task and the latest two turns. The model is currently configured with a
    # 4096-token context; this conservative total character budget leaves room
    # for Gemma's turn markers and tokenization overhead.
    # Request normalization is transport-only and never mutates saved history.
    raw_first_user = next((str(item.get("content", "")) for item in messages if item.get("role") == "user"), "")
    if system_text and raw_first_user.startswith(system_text):
        raw_first_user = raw_first_user[len(system_text):].lstrip()
        raw_first_user = re.sub(r"^用户任务：\s*", "", raw_first_user, count=1)
    task_anchor = re.split(r"\n\s*\[项目状态", raw_first_user, maxsplit=1)[0].strip()
    task_anchor = _bounded_chat_content(task_anchor, 220)
    generated_user_prefixes = (
        "[项目状态]", "操作结果（", "[模式切换]", "当前是提问模式：", "当前处于提问模式。",
        "计划已记录。", "你的上一条回复包含多个动作对象。", "你还没有完成请求的操作：",
        "上一条回复未能解析成动作。", "这个开发请求尚未执行。", "可复用的已验证经验；",
        "上一轮生成已因暂停作废。", "检测到工具失败（类型：", "已阻止重复安装：",
        "已阻止重复失败操作：",
    )
    recent_user_instructions = [
        str(item.get("content", "")).strip()
        for item in messages
        if item.get("role") == "user"
        and str(item.get("content", "")).strip()
        and str(item.get("content", "")).strip() != raw_first_user.strip()
        and not str(item.get("content", "")).lstrip().startswith(generated_user_prefixes)
    ][-3:]
    instruction_context = "\n".join(recent_user_instructions)
    instruction_context = _bounded_chat_content(instruction_context, 300)
    request_messages = [dict(item) for item in chat_messages[-4:]]
    if request_messages and request_messages[0]["role"] == "assistant":
        request_messages.insert(0, {"role": "user", "content": "初始任务：\n" + task_anchor})
    elif request_messages:
        request_messages[0]["content"] += "\n\n初始任务：\n" + task_anchor
    if request_messages and instruction_context:
        request_messages[0]["content"] += "\n\n最近用户明确指示（优先遵守）：\n" + instruction_context
    if system_text and request_messages:
        request_messages[0]["content"] = system_text + "\n\n用户任务与近期上下文：\n" + request_messages[0]["content"]

    for index, item in enumerate(request_messages):
        if index == 0:
            limit = 2800
        elif index == len(request_messages) - 1:
            limit = 2000 if "[项目状态]" in item["content"] else 500
        elif index == len(request_messages) - 2:
            limit = 180
        else:
            limit = 100
        item["content"] = _bounded_chat_content(item["content"], limit)

    def content_size() -> int:
        return sum(len(item["content"]) for item in request_messages)

    # Remove the oldest complete assistant/user pair while over budget, keeping
    # the task anchor and the most recent model action/result intact.
    while content_size() > MAX_OLLAMA_CONTEXT_CHARS and len(request_messages) > 3:
        if [item["role"] for item in request_messages[1:3]] == ["assistant", "user"]:
            del request_messages[1:3]
        else:
            break
    if content_size() > MAX_OLLAMA_CONTEXT_CHARS and request_messages:
        remaining = MAX_OLLAMA_CONTEXT_CHARS - sum(len(item["content"]) for item in request_messages[1:])
        request_messages[0]["content"] = _bounded_chat_content(request_messages[0]["content"], max(1, remaining))
    for index, item in enumerate(request_messages):
        expected = "user" if index % 2 == 0 else "assistant"
        if item["role"] != expected:
            raise ValueError(f"构造的 Ollama 消息角色不交替：第 {index + 1} 条应为 {expected}，实际为 {item['role']}")
    return request_messages


def ollama_chat(host: str, model: str, messages: list[dict[str, Any]], timeout: int, interrupt: threading.Event | None = None) -> dict[str, Any]:
    request_messages = build_ollama_messages(messages)
    url = host.rstrip("/") + "/api/chat"
    # Avoid Ollama's constrained JSON sampler: some GGUF/template combinations
    # reject it during sampler initialization. The system prompt requests JSON;
    # parse_decision validates and recovers the model's plain-text response.
    payload = json.dumps({"model": model, "messages": request_messages, "stream": False}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
        try:
            if interrupt is None:
                return json.loads(response.read().decode("utf-8"))
            body = bytearray()
            while True:
                if interrupt.is_set():
                    raise InterruptedError("用户请求暂停")
                chunk = response.read(4096)
                if not chunk:
                    break
                body.extend(chunk)
            return json.loads(body.decode("utf-8"))
        finally:
            response.close()
    except TimeoutError as exc:
        raise RuntimeError(f"等待本地模型超过 {timeout} 秒。可以用 --timeout 调高等待时间，或确认 Ollama 已加载 gemma-local。") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        role_trace = "/".join(item["role"] for item in request_messages)
        context_chars = sum(len(item["content"]) for item in request_messages)
        raise RuntimeError(
            f"Ollama 返回 HTTP {exc.code}（{url}）：{detail or exc.reason}"
            f"\n发送的角色顺序：{role_trace}"
            f"\n请求消息数：{len(request_messages)}；内容字符数：{context_chars}；字符预算：{MAX_OLLAMA_CONTEXT_CHARS}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"连接 Ollama 失败（{url}）：{exc}") from exc


def parse_decision(content: str) -> dict[str, Any] | None:
    """Parse the model's JSON action, tolerating surrounding whitespace/fences."""
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start:end + 1]
    try:
        decision = json.loads(candidate)
    except json.JSONDecodeError:
        # Small local models sometimes put literal line breaks inside a JSON
        # string (commonly write_file.content). Escape only control characters
        # encountered inside quoted strings, then retry without another LLM call.
        repaired: list[str] = []
        in_string = False
        escaped = False
        for char in candidate:
            if in_string and char in "\r\n\t":
                repaired.append({"\r": "\\r", "\n": "\\n", "\t": "\\t"}[char])
                escaped = False
                continue
            repaired.append(char)
            if escaped:
                escaped = False
            elif char == "\\" and in_string:
                escaped = True
            elif char == '"':
                in_string = not in_string
        try:
            decision = json.loads("".join(repaired))
        except json.JSONDecodeError:
            return None
    return decision if isinstance(decision, dict) else None


def parse_decisions(content: str) -> list[dict[str, Any]] | None:
    """Parse one or more concatenated JSON action objects, preserving order."""
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    decoder = json.JSONDecoder()
    decisions: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            break
        if text[index] != "{":
            return None
        try:
            decision, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            # Preserve the existing repair for literal control characters in strings.
            remaining = text[index:]
            repaired = parse_decision(remaining)
            if repaired is None:
                return None
            decisions.append(repaired)
            break
        if not isinstance(decision, dict):
            return None
        decisions.append(decision)
        index = end
    return decisions or None


def normalize_tool_args(decision: dict[str, Any]) -> dict[str, Any]:
    """Normalize common local-model tool argument placement mistakes."""
    raw_args = decision.get("args", {})
    args = dict(raw_args) if isinstance(raw_args, dict) else {}
    action = decision.get("action")
    aliases = {
        "list_files": {"path": ("directory", "dir", "folder", "target")},
        "read_file": {"path": ("file", "filepath", "file_path", "target")},
        "write_file": {
            "path": ("file", "filepath", "file_path", "target"),
            "content": ("text", "contents", "code", "body"),
        },
        "modify_file": {
            "path": ("file", "filepath", "file_path", "target"),
            "old_text": ("find", "search", "search_text", "old", "old_string", "before", "original"),
            "new_text": ("replace", "replace_text", "replacement", "new", "new_string", "after", "updated"),
            "replace_all": ("all",),
        },
        "set_project_goal": {"goal": ("project_goal", "objective", "target", "content", "text")},
        "create_directory": {"path": ("directory", "dir", "folder", "target")},
        "change_directory": {"path": ("directory", "dir", "folder", "target")},
        "move_file": {
            "source": ("from", "src", "source_path"),
            "destination": ("to", "dest", "dst", "target", "destination_path"),
        },
        "run_command": {"command": ("cmd", "shell", "script")},
    }
    for canonical, names in aliases.get(action, {}).items():
        if canonical not in args:
            value = next((args[name] for name in names if name in args), None)
            if value is None:
                value = next((decision[name] for name in (canonical, *names) if name in decision), None)
            if value is not None:
                args[canonical] = value
    return args


def normalize_model_action(decision: dict[str, Any]) -> dict[str, Any]:
    """Normalize common action aliases while preserving the existing tool set."""
    action = decision.get("action")
    if not isinstance(action, str):
        return decision
    aliases = {
        "ls": "list_files", "list_dir": "list_files", "list_directory": "list_files",
        "cat": "read_file", "read": "read_file", "read_text": "read_file", "open_file": "read_file",
        "write": "write_file", "write_text": "write_file", "create_file": "write_file",
        "modify": "modify_file", "edit_file": "modify_file", "patch_file": "modify_file",
        "set_goal": "set_project_goal", "project_goal": "set_project_goal",
        "update_goal": "set_project_goal", "update_project_goal": "set_project_goal",
        "set_project_objective": "set_project_goal",
        "mkdir": "create_directory", "make_directory": "create_directory", "create_folder": "create_directory",
        "cd": "change_directory", "change_folder": "change_directory",
        "set_working_directory": "change_directory", "set_current_directory": "change_directory",
        "move": "move_file", "rename_file": "move_file",
        "shell": "run_command", "run_shell": "run_command", "run_cmd": "run_command", "execute_command": "run_command",
        "command": "run_command", "build": "npm run build", "test": "npm test",
        "start_dev_server": "npm run dev", "install_dependencies": "npm install",
        "install_package": "npm install", "npm_install": "npm install",
        "final": "reply", "final_answer": "reply", "answer": "reply", "respond": "reply",
        "response": "reply", "replay": "reply", "reply_to_user": "reply",
        "respond_to_user": "reply", "chat_response": "reply", "chat": "reply", "say": "reply",
    }
    action = aliases.get(action, action)
    if action != decision.get("action"):
        decision = {**decision, "action": action}
    direct_commands = {
        "npm audit", "npm audit fix", "npm run dev", "npm run build", "npm run lint",
        "npm test", "npm run test", "npm run check", "npm run typecheck",
        "npm run preview", "npm run serve", "npm run watch", "npm start", "npm run start",
    }
    if action in direct_commands:
        normalized = dict(decision)
        normalized["action"] = "run_command"
        normalized["args"] = {"command": action}
        return normalized
    if action != "npm install":
        return decision

    raw_args = decision.get("args", {})
    args = raw_args if isinstance(raw_args, dict) else {}
    packages = args.get("packages", args.get("dependencies", args.get("package", args.get("package_name", args.get("name")))))
    if packages is None:
        package_specs: list[str] = []
    elif isinstance(packages, str):
        package_specs = [packages]
    elif isinstance(packages, list) and all(isinstance(item, str) for item in packages):
        package_specs = list(packages)
    else:
        return decision

    version = args.get("version")
    if version is not None and (not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._*+~^<>=-]+", version)):
        return decision
    for index, package in enumerate(package_specs):
        if not re.fullmatch(r"@?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?", package):
            return decision
        if version:
            package_specs[index] = f"{package}@{version}"

    normalized = dict(decision)
    normalized["action"] = "run_command"
    normalized["args"] = {"command": "npm install" + (" " + " ".join(package_specs) if package_specs else "")}
    return normalized


def is_safe_npm_install_command(command: str) -> bool:
    package_spec = r"@?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?(?:@[A-Za-z0-9._*+~^<>=-]+)?"
    return bool(re.fullmatch(r"npm install " + package_spec + r"(?:\s+" + package_spec + r")*", command))


def log_event(session_id: str, kind: str, detail: str) -> None:
    log_dir = CONVERSATION_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"{session_id}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "detail": detail}, ensure_ascii=False) + "\n")


EXPERIENCE_FILE = Path(__file__).resolve().parent / "conversations" / "experiences.json"


def classify_tool_failure(action: str, args: dict[str, Any], output: str) -> str | None:
    """Return a stable issue key when a tool result contains a real failure."""
    if output.startswith("用户拒绝执行"):
        return None
    if output.startswith("无需执行："):
        return None
    retry_result = output.split("自动重试结果：", 1)[-1] if "自动重试结果：" in output else ""
    if retry_result.startswith("退出码：0"):
        return None
    if action == "list_files" and "NotADirectoryError" in output:
        return "list_files_used_on_file"
    if action == "write_file" and (output.startswith("路径意图冲突：") or output.startswith("工具意图冲突：")):
        return "write_file_directory_misuse"
    if action in {"write_file", "create_directory"} and "路径冲突：" in output and "已是文件" in output:
        return "path_component_is_file"
    if "Missing script: \"dev\"" in output or "没有 scripts.dev" in output:
        return "npm_run_dev_script_missing"
    if "ETARGET" in output or "No matching version found" in output:
        return "npm_package_version_not_found"
    if "KeyError: 'command'" in output or "run_command 需要非空字符串 command" in output:
        return "run_command_argument_missing"
    if "已阻止重复安装" in output:
        return "repeat_install_without_config_change"
    exit_code = re.search(r"退出码：(-?\d+)", output)
    if (
        action == "run_command"
        and args.get("command") == "npm audit"
        and exit_code
        and int(exit_code.group(1)) != 0
        and re.search(r"found\s+\d+\s+vulnerabilit(?:y|ies)", output, re.IGNORECASE)
    ):
        return None
    if exit_code and int(exit_code.group(1)) != 0:
        return f"command_exit_{exit_code.group(1)}"
    if output.startswith(("工具执行失败：", "工具参数", "未知工具：", "后台命令启动失败：", "后台命令启动失败，", "命令未执行：", "移动失败：", "移动已取消：", "修改失败：", "设置失败：")) or ("未执行" in output and not output.startswith("用户拒绝执行")):
        return "tool_execution_failure"
    if output.startswith("切换失败："):
        return "tool_execution_failure"
    return None


def tool_succeeded(action: str, args: dict[str, Any], output: str) -> bool:
    if classify_tool_failure(action, args, output):
        return False
    if "自动重试结果：\n退出码：0" in output:
        return True
    if action == "write_file":
        return output.startswith("已写入：")
    if action == "modify_file":
        return output.startswith("已修改：")
    if action == "set_project_goal":
        return output == "项目目标已更新。"
    if action == "run_command":
        if args.get("command") == "npm audit" and _command_status(output, "npm audit")[0] == "completed_with_findings":
            return True
        return output.startswith(("退出码：0", "后台进程已启动，PID=", "无需执行："))
    return not output.startswith(("未知工具：", "工具参数", "工具执行失败：", "命令未执行："))


def failure_action_matches(
    record: dict[str, Any], action: str, args: dict[str, Any], current_directory: Path | None = None
) -> bool:
    if record.get("action") != action:
        return False
    recorded_args = record.get("args", {})
    if not isinstance(recorded_args, dict):
        return False
    recorded_directory = record.get("working_directory")
    if recorded_directory and current_directory is not None:
        if recorded_directory != str(current_directory.resolve()):
            return False
    keys_by_action = {
        "list_files": ("path",),
        "read_file": ("path",),
        "write_file": ("path",),
        "modify_file": ("path",),
        "set_project_goal": ("goal",),
        "create_directory": ("path",),
        "change_directory": ("path",),
        "move_file": ("source", "destination"),
        "run_command": ("command",),
    }
    keys = keys_by_action.get(action, ())
    return bool(keys) and all(
        recorded_args.get(key, "." if key == "path" and action == "list_files" else None)
        == args.get(key, "." if key == "path" and action == "list_files" else None)
        for key in keys
    )


def make_failure_record(
    action: str, args: dict[str, Any], output: str, current_directory: Path | None = None
) -> dict[str, Any]:
    safe_args = {key: value for key, value in args.items() if key != "content"}
    return {
        "action": action,
        "args": safe_args,
        "reason": classify_tool_failure(action, args, output) or "tool_execution_failure",
        "result": output[-800:],
        "working_directory": str(current_directory.resolve()) if current_directory else None,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def should_block_repeat(pending_issue: dict[str, Any] | None, recovery_actions: list[str], action: str, args: dict[str, Any]) -> bool:
    return bool(
        pending_issue
        and not recovery_actions
        and pending_issue.get("action") == action
        and pending_issue.get("args") == args
    )


def failure_lesson(issue: str) -> str:
    known = {
        "list_files_used_on_file": "list_files 的 path 必须是目录；读取文件内容要调用 read_file。",
        "write_file_directory_misuse": "创建目录必须用 create_directory；write_file 的 content 只会被保存为文件内容。",
        "path_component_is_file": "目标父路径实际是文件；先检查并保留移动该文件，再创建目录并重试目标文件写入。",
        "npm_run_dev_script_missing": 'npm run dev 只能在 package.json 存在 scripts.dev 时启动。静态 HTML 不需要 npm 开发服务器；Vite 项目需先确认脚本与 Vite 配置。',
        "npm_package_version_not_found": "ETARGET 表示声明的包版本无法从当前 registry 获取；检查包名、精确版本和 registry 后再选择兼容版本，不能无变化重试。",
        "run_command_argument_missing": 'run_command 的必需参数位于 args.command；工具参数错误时命令未执行。',
        "repeat_install_without_config_change": "package.json 与 lockfile 未变化时，重复 npm install 不会修复相同的解析错误；先查明并修正依赖声明或网络原因。",
    }
    return known.get(issue, "先根据实际工具输出确认根因，再修改相关配置并运行原失败步骤验证。")


def verification_succeeded(issue: str, failed_action: str, failed_args: dict[str, Any], action: str, args: dict[str, Any], output: str) -> bool:
    if not tool_succeeded(action, args, output):
        return False
    if issue == "list_files_used_on_file":
        return action == "read_file" and args.get("path") == failed_args.get("path") and not output.startswith("工具执行失败")
    if issue == "path_component_is_file":
        if action == "write_file":
            return tool_succeeded(action, args, output)
        if action == "create_directory":
            failed_path = failed_args.get("path", "")
            expected_parent = Path(failed_path).parent.as_posix()
            target = str(args.get("path", "")).replace("\\", "/").rstrip("/") or "."
            return target in {str(failed_path).replace("\\", "/").rstrip("/"), expected_parent} and tool_succeeded(action, args, output)
        return False
    if issue == "write_file_directory_misuse":
        failed_path = str(failed_args.get("path", "")).replace("\\", "/").rstrip("/")
        if action == "create_directory":
            created_path = str(args.get("path", "")).replace("\\", "/").rstrip("/")
            return created_path == failed_path and tool_succeeded(action, args, output)
        return action == "write_file" and tool_succeeded(action, args, output)
    if issue == "tool_execution_failure":
        if action != failed_action:
            return False
        if action == "run_command":
            return args.get("command") == failed_args.get("command") and tool_succeeded(action, args, output)
        return tool_succeeded(action, args, output)
    if issue == "npm_run_dev_script_missing" or issue.startswith("command_exit_"):
        if action != "run_command":
            return False
        old_command = failed_args.get("command")
        return args.get("command") == old_command and tool_succeeded(action, args, output)
    if issue in {"npm_package_version_not_found", "repeat_install_without_config_change"}:
        return action == "run_command" and args.get("command") == "npm install" and "退出码：0" in output
    if issue == "run_command_argument_missing":
        return action == "run_command" and isinstance(args.get("command"), str) and tool_succeeded(action, args, output)
    return False


def failure_recovery_prompt(issue: str, action: str, args: dict[str, Any], output: str, lesson: dict[str, Any] | None = None) -> str:
    """Require evidence-based diagnosis and a successful regression check."""
    details = {
        "list_files_used_on_file": "list_files 只能列目录。目标是文件时用 read_file 读取；不要据此声称没有文件系统访问能力。",
        "write_file_directory_misuse": "如果要创建目录，调用 create_directory，不能把 mkdir 命令放入 write_file.content；之后再用 write_file 写入具体文件。",
        "path_component_is_file": "先 read_file 检查冲突文件；如果该路径确实需要作为目录，使用 move_file 移至一个不存在的工作区路径（拒绝覆盖），再 create_directory 并重试 write_file。若当前项目不需要该目录，选择正确的现有目录结构。",
        "npm_run_dev_script_missing": '先对照本轮项目状态和用户目标判断项目类型。静态 HTML 不需要 npm run dev，不要为消除这个提示而新增 Vite 配置；若证据确认是 Vite，再检查 package.json、入口和依赖为何缺少 scripts.dev，并按根因修复。',
        "npm_package_version_not_found": "先读取 package.json，定位报错包及版本；确认可用且兼容的版本后再改配置。不要原样重试。",
        "run_command_argument_missing": 'run_command 参数必须是 {"args":{"command":"..."}}；先按该结构提出动作，再执行。',
    }
    diagnosis = details.get(issue, "先保留完整错误输出，检查与失败操作直接相关的文件/配置，提出一个有证据支持的原因。")
    prior = ""
    if lesson:
        prior = f"\n已验证的经验：{lesson.get('lesson', '')}；回归证据：{lesson.get('proof', '')}。先确认当前条件相同再采用。"
    completion = (
        "若项目状态证实 npm run dev 对静态 HTML 项目并无必要，应明确记录这是不适用的命令，不要为它创建 Vite 配置或强行重跑；若确认是 Vite，则修复后重新运行 npm run dev。"
        if issue == "npm_run_dev_script_missing"
        else "重新运行造成故障的同一项验证。"
    )
    closing = (
        "静态 HTML 情况下只可说明无需 npm 开发脚本，不得声称服务器已启动；Vite 情况下必须等重跑成功才可声称修复。"
        if issue == "npm_run_dev_script_missing"
        else "只有验证成功才算修复；如果仍失败，继续依据新输出诊断，不要重复未改变状态的同一操作，也不要声称已解决。"
    )
    return (
        f"检测到工具失败（类型：{issue}，动作：{action}，参数：{json.dumps(args, ensure_ascii=False)}）。\n"
        f"结果：{output[:5000]}\n诊断要求：{diagnosis}{prior}\n"
        f"恢复流程：先检查并记录证据；根据证据实施最小修复；{completion}"
        f"{closing}"
    )


def load_experiences(path: Path = EXPERIENCE_FILE) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def record_verified_experience(entry: dict[str, Any], path: Path = EXPERIENCE_FILE) -> None:
    """Persist only closed-loop lessons with an explicit successful proof."""
    if not entry.get("issue") or not entry.get("lesson") or not entry.get("proof"):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [item for item in load_experiences(path) if item.get("issue") != entry["issue"]]
    entries.append({**entry, "verified_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(entries[-50:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def pause_and_steer(messages: list[dict[str, Any]], persist: Any, session_id: str, pending: str = "", input_queue: queue.Queue[str] | None = None) -> str:
    """Pause at a safe boundary, collect steering, and choose resume or restart."""
    print("\n已暂停。输入新方向；/resume 继续当前任务，/restart 用新方向重开本轮。")
    while True:
        steering = input_queue.get().strip() if input_queue is not None else input("暂停中> ").strip()
        if input_queue is not None:
            print(f"暂停中> {steering}")
        if steering == "/resume":
            return pending
        if steering == "/restart":
            return ""
        if steering:
            messages.append({"role": "user", "content": f"[暂停期间的新指令] {steering}"})
            log_event(session_id, "steering", steering)
            persist()
            return pending


def auto_repair_known_npm_error(workspace: Path, output: str) -> str | None:
    """Apply narrow, deterministic fixes for known registry version mistakes."""
    if not re.search(r"No matching version found for vue@2\.7\.17\b", output):
        return None
    package_file = workspace / "package.json"
    try:
        package = json.loads(package_file.read_text(encoding="utf-8"))
    except Exception:
        return "识别到 vue@2.7.17 不存在，但无法读取 package.json；请修复配置后重试。"

    changed: list[str] = []
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        deps = package.get(section, {})
        if not isinstance(deps, dict):
            continue
        for name in ("vue", "vue-template-compiler"):
            if deps.get(name) == "2.7.17":
                deps[name] = "2.7.16"
                changed.append(f"{name}: 2.7.17 → 2.7.16")
    if not changed:
        return "识别到 vue@2.7.17 不存在，但 package.json 中未找到可自动修正的精确版本；请检查依赖声明。"
    package_file.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "已自动修正 package.json：" + "，".join(changed) + "。请继续 npm install，再运行构建验证。"


def check_model(host: str, model: str) -> None:
    """Fail early with actionable guidance instead of waiting at the prompt."""
    url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"无法连接 Ollama：{url}\n请先启动 Ollama，再重新运行 Agent。\n详情：{exc}") from exc

    installed = [entry.get("name", "") for entry in data.get("models", [])]
    if model not in installed and f"{model}:latest" not in installed:
        choices = ", ".join(installed) if installed else "（没有已安装模型）"
        raise RuntimeError(f"Ollama 已连接，但找不到模型 {model!r}。\n当前模型：{choices}\n请用 --model 指定 `ollama list` 中显示的名称。")


CONVERSATION_DIR = Path(__file__).resolve().parent / "conversations"


def project_state_path(workspace: Path) -> Path:
    """Keep durable project observations outside the user's source tree."""
    key = hashlib.sha256(str(workspace.resolve()).casefold().encode("utf-8")).hexdigest()[:20]
    return CONVERSATION_DIR / "project_states" / f"{key}.json"


def load_project_state(workspace: Path) -> dict[str, Any]:
    try:
        value = json.loads(project_state_path(workspace).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return None, "根值不是 JSON 对象"
    return value, None


def _workspace_file_exists(workspace: Path, relative: str) -> bool:
    return (workspace / relative).is_file()


def _workspace_structure(workspace: Path, limit: int = 60) -> dict[str, Any]:
    """Record the observed workspace tree without imposing a project template."""
    ignored = {".git", "node_modules", ".venv", "__pycache__"}
    entries: list[str] = []
    for current, directories, files in os.walk(workspace):
        directories[:] = sorted(
            name for name in directories
            if name not in ignored and not (Path(current) / name).is_symlink()
        )
        current_path = Path(current)
        relative_dir = current_path.relative_to(workspace)
        for name in sorted(directories):
            relative = (relative_dir / name).as_posix()
            entries.append(relative + "/")
            if len(entries) >= limit:
                return {"entries": entries, "truncated": True}
        for name in sorted(files):
            relative = (relative_dir / name).as_posix()
            entries.append(relative)
            if len(entries) >= limit:
                return {"entries": entries, "truncated": True}
    return {"entries": entries, "truncated": False}


def _dependency_package_exists(workspace: Path, name: str) -> bool:
    # Check direct dependency paths only; do not traverse the potentially huge node_modules tree.
    parts = name.split("/") if name.startswith("@") else [name]
    if any(not part or part in {".", ".."} or "\\" in part for part in parts):
        return False
    return (workspace / "node_modules" / Path(*parts) / "package.json").is_file()


def _derive_project_snapshot(
    workspace: Path,
    previous: dict[str, Any],
    structure_root: Path | None = None,
) -> dict[str, Any]:
    package, package_error = _read_json_object(workspace / "package.json")
    lock, lock_error = _read_json_object(workspace / "package-lock.json")
    dependency_sections = ("dependencies", "devDependencies", "optionalDependencies")
    dependencies: dict[str, str] = {}
    if package:
        for section in dependency_sections:
            values = package.get(section, {})
            if isinstance(values, dict):
                dependencies.update({str(name): str(version) for name, version in values.items()})
    scripts = package.get("scripts", {}) if package else {}
    if not isinstance(scripts, dict):
        scripts = {}

    files = {
        "index.html": _workspace_file_exists(workspace, "index.html"),
        "src/index.html": _workspace_file_exists(workspace, "src/index.html"),
        "src/main.js": _workspace_file_exists(workspace, "src/main.js"),
        "src/main.ts": _workspace_file_exists(workspace, "src/main.ts"),
        "src/main.jsx": _workspace_file_exists(workspace, "src/main.jsx"),
        "src/main.tsx": _workspace_file_exists(workspace, "src/main.tsx"),
        "src/App.vue": _workspace_file_exists(workspace, "src/App.vue"),
        "src/App.jsx": _workspace_file_exists(workspace, "src/App.jsx"),
        "src/App.tsx": _workspace_file_exists(workspace, "src/App.tsx"),
        "vite.config.js": _workspace_file_exists(workspace, "vite.config.js"),
        "vite.config.mjs": _workspace_file_exists(workspace, "vite.config.mjs"),
        "vite.config.ts": _workspace_file_exists(workspace, "vite.config.ts"),
        "dist/index.html": _workspace_file_exists(workspace, "dist/index.html"),
    }
    html_text = ""
    if files["index.html"]:
        try:
            html_text = (workspace / "index.html").read_text(encoding="utf-8", errors="replace")[:50000]
        except OSError:
            pass
    html_entry_refs = re.findall(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", html_text, re.IGNORECASE)
    referenced_entries = []
    for ref in html_entry_refs:
        normalized = ref.split("?", 1)[0].split("#", 1)[0].lstrip("/").replace("\\", "/")
        if normalized and not re.match(r"^(?:https?:)?//", ref):
            referenced_entries.append({"path": normalized, "exists": (workspace / normalized).is_file()})

    lock_packages = lock.get("packages", {}) if lock else {}
    lock_root = lock_packages.get("") if isinstance(lock_packages, dict) else None
    lock_status = "missing" if lock is None and lock_error is None else "invalid" if lock_error else "present"
    lock_sync = "unknown"
    if lock and isinstance(lock_root, dict) and package:
        lock_sync = "matches_manifest" if all(
            lock_root.get(section, {}) == (package.get(section, {}) if isinstance(package.get(section, {}), dict) else {})
            for section in dependency_sections
        ) else "differs_from_manifest"
    lock_package_count = max(0, len(lock_packages) - 1) if isinstance(lock_packages, dict) else None
    current_dependency_signature = dependency_signature(workspace)
    direct_install_presence = {
        name: _dependency_package_exists(workspace, name)
        for name in list(dependencies)[:30]
    }
    dependency_presence_truncated = len(dependencies) > 30

    vite_dependency = "vite" in dependencies
    vite_script = any(isinstance(value, str) and re.search(r"(?:^|[\s/&])vite(?:\s|$)", value) for value in scripts.values())
    vite_config = any(files[name] for name in ("vite.config.js", "vite.config.mjs", "vite.config.ts"))
    has_vue = "vue" in dependencies or files["src/App.vue"]
    has_react = any(name in dependencies for name in ("react", "react-dom")) or files["src/App.jsx"] or files["src/App.tsx"]
    if vite_dependency or vite_script or vite_config:
        project_type = "vite"
        confidence = "high"
        project_evidence = [item for item, condition in (
            ("package.json 声明 vite", vite_dependency),
            ("package.json 脚本调用 vite", vite_script),
            ("存在 vite.config 文件", vite_config),
        ) if condition]
        if has_vue:
            project_type = "vue_vite"
            project_evidence.append("检测到 Vue 依赖或 src/App.vue")
        elif has_react:
            project_type = "react_vite"
            project_evidence.append("检测到 React 依赖或 JSX 入口")
    elif has_vue:
        project_type = "vue_without_vite"
        confidence = "medium"
        project_evidence = ["检测到 Vue 依赖或 .vue 源码", "未检测到 Vite 依赖、脚本或配置"]
    elif has_react:
        project_type = "react_without_vite"
        confidence = "medium"
        project_evidence = ["检测到 React 依赖或 JSX 源码", "未检测到 Vite 依赖、脚本或配置"]
    elif files["index.html"] or files["src/index.html"]:
        project_type = "static_html"
        confidence = "medium"
        project_evidence = ["检测到 HTML 入口", "未检测到 Vite 配置或 Vite 依赖"]
        if files["src/index.html"] and not files["index.html"]:
            project_evidence.append("HTML 入口位于 src/index.html，而不是项目根目录")
    elif (workspace / "package.json").exists() and package is None:
        project_type = "invalid_package_manifest"
        confidence = "high"
        project_evidence = ["package.json 存在但无法解析为有效 JSON", package_error or "无法读取清单"]
    elif package and dependencies:
        project_type = "node_or_other"
        confidence = "low"
        project_evidence = ["package.json 声明依赖，但未检测到 HTML 入口或 Vite 配置"]
    elif package:
        project_type = "unconfigured_node_manifest"
        confidence = "high"
        project_evidence = ["存在 package.json，但未检测到应用入口或 Vite 依赖/配置"]
    else:
        project_type = "unknown"
        confidence = "low"
        project_evidence = ["当前扫描的项目特征不足以判断技术栈"]

    missing: list[str] = []
    if project_type == "invalid_package_manifest":
        completeness = "incomplete"
        missing = ["有效的 package.json"]
        completeness_note = "package.json 当前无法解析；应先依据错误位置修正 JSON，再继续判断依赖和脚本。"
    elif project_type == "static_html":
        if not files["index.html"]:
            missing.append("项目根目录 index.html（静态页面入口）")
        missing_refs = [item["path"] for item in referenced_entries if not item["exists"]]
        if missing_refs:
            missing.append("HTML 引用的本地脚本不存在：" + ", ".join(missing_refs))
        completeness = "ready" if not missing else "incomplete"
        completeness_note = "静态 HTML 无需 package.json 或 npm 开发脚本。" if completeness == "ready" else "找到 HTML，但标准入口位置不完整。"
    elif project_type in {"vite", "vue_vite", "react_vite"}:
        if package is None:
            missing.append("有效的 package.json")
        if not vite_dependency:
            missing.append("依赖 vite")
        elif not direct_install_presence.get("vite", False):
            missing.append("本地 node_modules 中的 vite（需按当前清单安装依赖）")
        absent_dependencies = [name for name, present in direct_install_presence.items() if not present]
        if absent_dependencies and "vite" not in absent_dependencies:
            missing.append("本地 node_modules 缺少直接依赖：" + ", ".join(absent_dependencies))
        if lock_status == "invalid":
            missing.append("package-lock.json 不是有效 JSON")
        if lock_sync == "differs_from_manifest":
            missing.append("package-lock.json 中的根依赖声明与 package.json 不一致")
        previous_install = previous.get("npm_install", {}) if isinstance(previous.get("npm_install"), dict) else {}
        if (
            previous_install.get("status") in {"succeeded", "stale_after_manifest_change"}
            and previous_install.get("dependency_signature") != current_dependency_signature
        ):
            missing.append("npm install 的成功记录对应旧清单；依赖变更后尚未重新安装")
        if not isinstance(scripts.get("dev"), str) or not scripts.get("dev", "").strip():
            missing.append("scripts.dev")
        if not files["index.html"]:
            missing.append("项目根目录 index.html")
        has_application_entry = any(files[key] for key in ("src/main.js", "src/main.ts", "src/main.jsx", "src/main.tsx"))
        has_existing_referenced_entry = any(item["exists"] for item in referenced_entries)
        if not has_application_entry and not has_existing_referenced_entry:
            missing.append("应用入口模块（例如 src/main.js）")
        if project_type == "vue_vite":
            vue_version = dependencies.get("vue", "")
            plugin_name = "@vitejs/plugin-vue2" if vue_version.startswith("2.") else "@vitejs/plugin-vue"
            if files["src/App.vue"] and plugin_name not in dependencies:
                missing.append(f"Vue SFC 插件 {plugin_name}")
            elif files["src/App.vue"] and not direct_install_presence.get(plugin_name, False):
                missing.append(f"本地 node_modules 中的 Vue SFC 插件 {plugin_name}")
        completeness = "ready" if not missing else "incomplete"
        completeness_note = "Vite 项目的关键运行配置已具备；仍需实际运行构建/启动验证。" if completeness == "ready" else "Vite 项目缺少必需配置项。"
        if dependency_presence_truncated:
            completeness_note += " 直接依赖超过扫描上限，部分安装状态未逐项检查。"
    elif project_type in {"vue_without_vite", "react_without_vite"}:
        completeness = "incomplete"
        missing = ["项目构建/开发服务器配置（当前依赖了框架但未检测到 Vite）"]
        if not files["index.html"]:
            missing.append("项目根目录 index.html")
        completeness_note = "当前依赖或源码指向框架项目，但缺少 Vite 配置；不能把它当成无需 npm 的静态 HTML。先按用户要求确认应使用的构建工具。"
    elif project_type == "unconfigured_node_manifest":
        completeness = "incomplete"
        completeness_note = "npm init 生成的通用清单不等于应用骨架；应依据用户要求选择静态页面或框架，再补齐入口和配置。"
        if not scripts:
            missing.append("应用脚本（当前只有通用 npm 清单）")
        if not files["index.html"]:
            missing.append("应用入口文件")
    else:
        completeness = "unknown"
        completeness_note = "证据不足；应先依据用户要求检查/确定项目类型，不要仅凭 package.json 或 npm 成功结果假设项目已配置完成。"

    missing = list(dict.fromkeys(missing))
    signature = current_dependency_signature
    npm_install = dict(previous.get("npm_install", {})) if isinstance(previous.get("npm_install"), dict) else {}
    if npm_install.get("status") == "succeeded" and npm_install.get("dependency_signature") != signature:
        npm_install["status"] = "stale_after_manifest_change"
        npm_install["interpretation"] = "package.json 或 package-lock.json 在成功安装后发生变化；旧安装结果不能证明当前清单已安装。"

    last = previous.get("last_operation", {}) if isinstance(previous.get("last_operation"), dict) else {}
    interpretation: list[str] = []
    if last.get("action") == "run_command":
        command = str(last.get("command", ""))
        outcome = last.get("status", "unknown")
        if command == "npm init -y" and outcome == "succeeded":
            interpretation.append("npm init -y 已生成通用包清单；它不会创建 Vite 脚本、依赖或应用入口。当前清单/入口事实见上方。")
        elif command == "npm install" and outcome == "succeeded":
            if not dependencies:
                interpretation.append("npm install 返回成功，但 package.json 没有声明依赖；这不表示 Vue/Vite 等框架已安装。审计数量也不是直接依赖数量。")
            else:
                absent = [name for name, present in direct_install_presence.items() if not present]
                if absent:
                    interpretation.append("npm install 返回成功，但扫描发现部分直接依赖目录缺失：" + ", ".join(absent) + "。不能仅凭退出码确认运行环境完整。")
                elif dependency_presence_truncated:
                    interpretation.append("npm install 返回成功，扫描确认前 30 项直接依赖目录存在；其余依赖未逐项检查。结果仅适用于当前清单指纹。")
                else:
                    interpretation.append("npm install 返回成功，且直接依赖目录存在；结果仅适用于当前 package.json/package-lock 指纹。")
        elif command == "npm run dev" and outcome in {"not_executed", "not_required"}:
            if project_type == "static_html":
                interpretation.append("Agent 在启动前置检查中未执行 npm run dev；检测到静态 HTML 项目，该项目不需要 npm 开发脚本。")
            else:
                interpretation.append("Agent 在启动前置检查中未执行 npm run dev；当前项目状态缺少 scripts.dev，需要结合用户目标判断应配置 Vite 还是无需 npm。")
        elif command == "npm audit" and outcome == "completed_with_findings":
            interpretation.append("npm audit 已完成并报告漏洞；非零退出码表示发现项，不代表 npm install 失败。应按用户要求评估，不要自动升级依赖。")
        elif command == "npm audit" and outcome == "succeeded":
            if last.get("npm_audit_vulnerability_count") == 0:
                interpretation.append("npm audit 返回成功并报告 0 个漏洞；这是安全审计结果，不是依赖安装结果。")
            else:
                interpretation.append("npm audit 返回成功；应以命令输出中的审计结果为准。")
        elif command == "npm run build" and outcome == "succeeded":
            if project_type in {"vite", "vue_vite", "react_vite"} and files["dist/index.html"]:
                interpretation.append("构建脚本返回成功，并检测到 dist/index.html 构建产物。")
            elif project_type in {"vite", "vue_vite", "react_vite"}:
                interpretation.append("构建脚本返回成功，但没有检测到常见的 dist/index.html 产物；以脚本实际输出目录和内容为准。")
            else:
                interpretation.append("构建脚本返回成功；应结合命令输出确认它生成了目标产物。")
        elif command == "npm run dev" and outcome == "started":
            interpretation.append("开发服务器进程已启动；记录的本地地址以命令实际输出为准。")
        elif outcome == "succeeded" and command.startswith("npm run "):
            interpretation.append(f"{command} 返回成功；它验证了该脚本本次可执行，但仍需结合入口文件和项目配置判断完整性。")
        elif outcome == "failed":
            interpretation.append(f"{command} 失败；必须根据错误输出和上方状态定位原因，不能重复无变化的命令。")
    if not interpretation:
        interpretation.append(completeness_note)

    if not PROJECT_TYPE_DETECTION_ENABLED:
        project_type = "disabled"
        confidence = "disabled"
        project_evidence = ["自动项目类型判断已暂时停用"]
        completeness = "disabled"
        missing = []
        completeness_note = "自动项目结构完整度判断已暂时停用；请依据实际工作目录和文件操作结果继续。"
        interpretation = [completeness_note]

    return {
        "schema_version": 1,
        "workspace": str(workspace.resolve()),
        "current_structure": _workspace_structure(structure_root or workspace),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project_type": {"value": project_type, "confidence": confidence, "evidence": project_evidence},
        "configuration": {"status": completeness, "missing": missing, "note": completeness_note},
        "package": {
            "exists": (workspace / "package.json").is_file(),
            "valid": package is not None,
            "parse_error": package_error,
            "name": package.get("name") if package else None,
            "scripts": scripts,
            "dependencies": dependencies,
            "direct_dependency_count": len(dependencies),
            "direct_dependency_directories_present": direct_install_presence,
            "direct_dependency_presence_scan_truncated": dependency_presence_truncated,
        },
        "lockfile": {
            "status": lock_status,
            "parse_error": lock_error,
            "lockfile_version": lock.get("lockfileVersion") if lock else None,
            "locked_package_count_excluding_root": lock_package_count,
            "manifest_dependency_sync": lock_sync,
        },
        "files": files,
        "html_script_entries": referenced_entries,
        "npm_install": npm_install,
        "last_operation": last,
        "interpretation": interpretation,
        "observations": previous.get("observations", [])[-10:] if isinstance(previous.get("observations", []), list) else [],
    }


def _command_status(output: str, command: str = "") -> tuple[str, int | None]:
    if "自动重试结果：" in output:
        output = output.split("自动重试结果：", 1)[-1]
    exit_match = re.search(r"退出码：(-?\d+)", output)
    if exit_match:
        code = int(exit_match.group(1))
        if (
            command == "npm audit"
            and code != 0
            and re.search(r"found\s+\d+\s+vulnerabilit(?:y|ies)", output, re.IGNORECASE)
        ):
            return "completed_with_findings", code
        return ("succeeded" if code == 0 else "failed"), code
    if output.startswith("后台进程已启动，PID="):
        return "started", None
    if output.startswith("无需执行："):
        return "not_required", None
    if output.startswith(("命令未执行：", "未执行 ", "已阻止", "用户拒绝执行")) or "命令未执行：" in output:
        return "not_executed", None
    if output.startswith("后台命令启动失败"):
        return "failed", None
    return "unknown", None


def _interpret_action_result(action: str, args: dict[str, Any], output: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    safe_args = {key: value for key, value in args.items() if key != "content"}
    record: dict[str, Any] = {"action": action, "args": safe_args, "status": "succeeded" if tool_succeeded(action, args, output) else "failed"}
    if action == "run_command":
        command = str(args.get("command", ""))
        status, exit_code = _command_status(output, command)
        record.update({"command": command, "status": status, "exit_code": exit_code, "output_excerpt": output[-3000:]})
        audited = re.search(r"audited\s+(\d+)\s+packages?", output, re.IGNORECASE)
        if audited:
            record["npm_audited_package_count"] = int(audited.group(1))
        for label in ("added", "removed", "changed"):
            count = re.search(rf"\b{label}\s+(\d+)\s+packages?\b", output, re.IGNORECASE)
            if count:
                record[f"npm_{label}_package_count"] = int(count.group(1))
        vulnerabilities = re.search(r"found\s+(\d+)\s+vulnerabilit(?:y|ies)", output, re.IGNORECASE)
        if vulnerabilities:
            record["npm_audit_vulnerability_count"] = int(vulnerabilities.group(1))
        local_url = re.search(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?/?", output)
        if local_url and command == "npm run dev":
            record["server_url"] = local_url.group(0)
        if command == "npm install":
            signature = dependency_signature(Path(snapshot["workspace"]))
            dependency_count = snapshot["package"]["direct_dependency_count"]
            if status == "succeeded":
                meaning = "npm 成功处理当前清单。" if dependency_count else "npm 成功处理当前清单，但清单没有声明应用依赖；不能据此认定框架已安装。"
                snapshot["npm_install"] = {
                    "status": "succeeded",
                    "dependency_signature": signature,
                    "direct_dependency_count": dependency_count,
                    "audited_package_count": int(audited.group(1)) if audited else None,
                    "output_excerpt": output[-1500:],
                    "interpretation": meaning,
                    "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            else:
                snapshot["npm_install"] = {
                    "status": status,
                    "dependency_signature": signature,
                    "direct_dependency_count": dependency_count,
                    "output_excerpt": output[-1500:],
                    "interpretation": "安装未成功；根据错误输出检查声明、锁文件和环境。",
                    "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
        output_lines = [line.strip() for line in output.splitlines() if line.strip()]
        record["output_summary"] = " | ".join(output_lines[:5])[:800]
    else:
        if action == "read_file":
            if record["status"] == "succeeded":
                record["output_summary"] = f"成功读取文件内容（{len(output)} 个字符）；内容保留在会话工具结果中，不复制到项目状态记忆。"
            else:
                record["output_summary"] = output[:500]
        else:
            record["output_excerpt"] = output[-1200:]
            record["output_summary"] = output.splitlines()[0][:500] if output else "（空输出）"
    return record


def update_project_state(
    workspace: Path,
    previous: dict[str, Any] | None = None,
    action: str | None = None,
    args: dict[str, Any] | None = None,
    output: str = "",
    rationale: str = "",
    *,
    user_input: str | None = None,
    current_directory: Path | None = None,
    goal_achieved: bool | None = None,
    project_goal: str | None = None,
) -> dict[str, Any]:
    previous = previous if isinstance(previous, dict) else load_project_state(workspace)
    snapshot = _derive_project_snapshot(workspace, previous, current_directory)
    prior_failure_detected = previous.get("failure_detected") == "是"
    prior_failure_record = previous.get("failure_record")
    snapshot["failure_detected"] = "是" if prior_failure_detected else "否"
    snapshot["failure_record"] = prior_failure_record
    if project_goal is not None:
        snapshot["goal"] = project_goal
        snapshot["goal_achieved"] = None
    if user_input is not None:
        snapshot["recent_user_input"] = user_input[:2000]
        if user_input.strip() and (not previous.get("goal") or previous.get("goal_achieved") is True):
            snapshot["goal"] = user_input[:2000]
        if user_input.strip():
            snapshot["goal_achieved"] = None
    elif "recent_user_input" in previous:
        snapshot["recent_user_input"] = previous["recent_user_input"]
    if current_directory is not None:
        try:
            snapshot["working_directory"] = current_directory.resolve().relative_to(workspace.resolve()).as_posix() or "."
        except ValueError:
            snapshot["working_directory"] = "."
    else:
        snapshot["working_directory"] = previous.get("working_directory", ".")
    if goal_achieved is not None:
        snapshot["goal_achieved"] = goal_achieved
    elif "goal_achieved" in previous:
        snapshot["goal_achieved"] = previous["goal_achieved"]
    if action:
        snapshot["goal_achieved"] = None
    if action:
        recorded_working_directory = snapshot.get("working_directory", ".")
        operation = _interpret_action_result(action, args or {}, output, snapshot)
        if rationale:
            operation["agent_rationale"] = str(rationale)[:1000]
        snapshot["last_operation"] = operation
        observations = list(snapshot.get("observations", []))
        observation = {key: operation.get(key) for key in ("action", "command", "status", "exit_code", "output_summary", "npm_audited_package_count", "npm_added_package_count", "npm_removed_package_count", "npm_changed_package_count", "npm_audit_vulnerability_count", "server_url", "agent_rationale") if key in operation}
        observation.update({
            "project_type": snapshot["project_type"]["value"],
            "configuration_status": snapshot["configuration"]["status"],
            "configuration_missing": snapshot["configuration"]["missing"],
            "dependency_signature": dependency_signature(workspace),
        })
        observations.append(observation)
        snapshot["observations"] = observations[-10:]
        refreshed = _derive_project_snapshot(workspace, snapshot, current_directory)
        refreshed["last_operation"] = operation
        refreshed["observations"] = snapshot["observations"]
        snapshot = refreshed
        snapshot["goal"] = project_goal if project_goal is not None else previous.get("goal", snapshot.get("goal", ""))
        snapshot["recent_user_input"] = previous.get("recent_user_input", snapshot.get("recent_user_input", ""))
        snapshot["working_directory"] = recorded_working_directory
        snapshot["goal_achieved"] = None
        snapshot["failure_detected"] = "是" if prior_failure_detected else "否"
        snapshot["failure_record"] = prior_failure_record
        action_args = args or {}
        succeeded = tool_succeeded(action, action_args, output)
        if not succeeded:
            if not prior_failure_detected:
                snapshot["failure_record"] = make_failure_record(action, action_args, output, current_directory)
            snapshot["failure_detected"] = "是"
        elif prior_failure_detected and isinstance(prior_failure_record, dict):
            if failure_action_matches(prior_failure_record, action, action_args, current_directory):
                snapshot["failure_detected"] = "否"
        elif not prior_failure_detected and "自动重试结果：\n退出码：0" in output:
            initial_output = output.split("\n自动诊断与修复：", 1)[0]
            initial_status, _ = _command_status(initial_output, str(action_args.get("command", "")))
            if initial_status == "failed":
                snapshot["failure_record"] = make_failure_record(
                    action, action_args, initial_output, current_directory
                )
    path = project_state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)
    return snapshot


def format_project_state(snapshot: dict[str, Any]) -> str:
    """Expose recorded facts and recent progress without prescribing a file layout."""
    last_operation = snapshot.get("last_operation", {})
    if isinstance(last_operation, dict):
        last_operation = {
            "action": last_operation.get("action"),
            "args": last_operation.get("args", {}),
            "status": last_operation.get("status"),
            "result": str(last_operation.get("output_summary", last_operation.get("output_excerpt", "")))[:500],
        }
    structure = snapshot.get("current_structure", {})
    entries = list(structure.get("entries", []))
    structure_truncated = bool(structure.get("truncated", False))
    selected = {
        "项目目标": snapshot.get("goal", ""),
        "用户最近输入": snapshot.get("recent_user_input", ""),
        "失败记录": snapshot.get("failure_record"),
        "失败检测": snapshot.get("failure_detected", "否"),
        "上条动作结果": last_operation,
        "是否达到目的": snapshot.get("goal_achieved"),
        "上条动作": last_operation.get("action") if isinstance(last_operation, dict) else None,
        "当前工作目录": snapshot.get("working_directory", "."),
        "当前文件结构": entries,
        "结构是否截断": structure_truncated,
    }
    state_prefix = "[项目状态]\n"
    encoded = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
    while len(state_prefix) + len(encoded) > PROJECT_STATE_CONTEXT_CHARS and entries:
        entries.pop()
        structure_truncated = True
        selected["结构是否截断"] = structure_truncated
        encoded = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
    return state_prefix + encoded


def project_state_console_summary(snapshot: dict[str, Any]) -> str:
    parts = [f"[项目状态] 当前目录：{snapshot.get('working_directory', '.')}"]
    parts.append(f"[失败检测] {snapshot.get('failure_detected', '否')}")
    failure = snapshot.get("failure_record")
    if snapshot.get("failure_detected") == "是" and isinstance(failure, dict):
        parts.append(f"[失败记录] {failure.get('action', '')}：{failure.get('result', '')[:80]}")
    last = snapshot.get("last_operation", {})
    if isinstance(last, dict) and last:
        result = str(last.get("output_summary", last.get("status", "")))
        if len(result) > 100:
            result = result[:97] + "..."
        parts.append(f"[上条动作] {last.get('action', '')} → {result}")
    achieved = snapshot.get("goal_achieved")
    if achieved is not None:
        parts.append("[目标状态] " + ("已达到" if achieved else "未达到"))
    return "\n".join(parts)


def save_conversation(session_id: str, messages: list[dict[str, Any]]) -> None:
    CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
    path = CONVERSATION_DIR / f"{session_id}.json"
    temp_path = path.with_suffix(".tmp")
    data = {"id": session_id, "updated": time.time(), "messages": messages}
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def load_conversation(session_id: str) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]+", session_id):
        raise ValueError("无效的会话编号")
    path = CONVERSATION_DIR / f"{session_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise ValueError("会话文件格式错误")
    return messages


def new_conversation_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}"


def conversation_summary(messages: list[dict[str, Any]]) -> str:
    first_user = next((str(item.get("content", "")) for item in messages if item.get("role") == "user"), "（空会话）")
    first_line = first_user.splitlines()[0].strip()
    return first_line[:72] + ("…" if len(first_line) > 72 else "")


def refresh_system_prompt(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure resumed conversations use the current tool rules and state format."""
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = SYSTEM_PROMPT
    else:
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description="使用本地 Ollama 模型自动开发网站")
    parser.add_argument("--workspace", default=".", help="网站项目目录，默认当前目录")
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "gemma-local"), help="Ollama 模型名称")
    parser.add_argument("--host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"), help="Ollama 地址")
    parser.add_argument("--timeout", type=int, default=900, help="每次等待模型回复的最长秒数，默认 900")
    opts = parser.parse_args()
    workspace = Path(opts.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        check_model(opts.host, opts.model)
    except RuntimeError as exc:
        print(f"启动失败：\n{exc}")
        return 1
    CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
    previous_sessions = sorted(CONVERSATION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if previous_sessions:
        try:
            session_id = previous_sessions[0].stem
            messages = load_conversation(session_id)
            print(f"已恢复上次对话：{session_id}")
        except Exception:
            session_id = new_conversation_id()
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    else:
        session_id = new_conversation_id()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages = refresh_system_prompt(messages)

    def persist() -> None:
        save_conversation(session_id, messages)

    mode = "agent"
    project_state = load_project_state(workspace)
    try:
        current_directory = path_in_workspace(workspace, str(project_state.get("working_directory", ".")))
        if not current_directory.is_dir():
            current_directory = workspace
    except (OSError, ValueError, TypeError):
        current_directory = workspace
    project_state = update_project_state(workspace, project_state, current_directory=current_directory)

    persist()
    print(f"网站开发 Agent 已就绪（{mode} 模式）\n工作区：{workspace}\n模型：{opts.model} @ {opts.host}\n/new 新对话  /history 历史  /resume <编号> 恢复对话  /mode ask|agent 切换模式  /pause 暂停/继续  /exit 退出。", flush=True)
    while True:
        try:
            prompt = input("\n你> ").strip()
            if not prompt:
                continue
            if prompt in {"/exit", "/quit"}:
                persist()
                return 0
            if prompt.startswith("/mode"):
                parts = prompt.split()
                if len(parts) == 2 and parts[1] in {"ask", "agent"}:
                    mode = parts[1]
                    messages.append({"role": "user", "content": f"[模式切换] 当前为 {mode} 模式。"})
                    persist()
                    log_event(session_id, "mode", mode)
                    print(f"已切换到 {mode} 模式；会话历史保留。")
                else:
                    print(f"当前模式：{mode}。用法：/mode ask 或 /mode agent")
                continue
            if prompt == "/pause":
                print("当前没有运行中的任务。任务执行期间可输入 /pause 请求暂停。")
                continue
            if prompt == "/new":
                persist()
                session_id = new_conversation_id()
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                persist()
                print(f"已开始新对话：{session_id}")
                continue
            if prompt == "/history":
                for session_file in sorted(CONVERSATION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                    try:
                        saved = load_conversation(session_file.stem)
                        print(f"{session_file.stem}  {conversation_summary(saved)}")
                    except Exception:
                        print(f"{session_file.stem}  （无法读取）")
                continue
            if prompt.startswith("/resume "):
                requested_id = prompt.split(maxsplit=1)[1].strip()
                try:
                    messages = refresh_system_prompt(load_conversation(requested_id))
                    session_id = requested_id
                    persist()
                    print(f"已恢复对话：{session_id}")
                except Exception as exc:
                    print(f"恢复失败：{exc}")
                continue
            # Put the compact, high-priority project-state header before the
            # directory listing and the raw prompt. Consecutive user messages
            # are merged for Ollama, so bounded context keeps the state header
            # and the newest prompt at opposite ends if trimming is needed.
            project_state = update_project_state(
                workspace, project_state, user_input=prompt, current_directory=current_directory
            )
            state_context = format_project_state(project_state)
            print(project_state_console_summary(project_state), flush=True)
            messages.append({"role": "user", "content": state_context})
            # Preserve dialogue so answers to clarification questions retain context.
            messages.append({"role": "user", "content": prompt})
            log_event(session_id, "project_state_context", state_context)
            if mode == "ask":
                messages.append({"role": "user", "content": "当前是提问模式：只解释、分析或协助制定计划，不调用工具、不修改文件、不运行命令。"})
            else:
                lessons = load_experiences()[-5:]
                if lessons:
                    lesson_text = "\n".join(f"- [{item.get('issue')}] {item.get('lesson')}（已验证：{item.get('proof')}）" for item in lessons)
                    messages.append({"role": "user", "content": "可复用的已验证经验；仅在当前条件吻合时采用：\n" + lesson_text})
            persist()
            log_event(session_id, "user", prompt)
            plan_shown = False
            operation_log: list[str] = []
            completed_commands: set[str] = set()
            invalid_json_retries = 0
            no_action_nudges = 0
            requires_work = bool(re.search(r"创建|开发|构建|安装|运行|修复|修改|添加|实现|启动|继续|编写|补齐|更新|部署|生成|执行", prompt))
            requires_write = bool(re.search(r"开发|构建.*(?:网站|网页|页面|项目|应用)|修改|添加|实现|编写|补齐|更新|部署|生成|创建.*(?:网站|网页|页面|项目|应用|文件|源码)", prompt))
            requires_install = bool(re.search(r"npm install|安装依赖|安装并构建", prompt, re.IGNORECASE))
            requires_build = bool(re.search(r"npm run build|构建项目|构建网站|继续构建|安装并构建", prompt, re.IGNORECASE)) and (current_directory / "package.json").is_file()
            written_files: set[str] = set()
            pending_command_failure: str | None = None
            pending_issue: dict[str, Any] | None = None
            recovery_actions: list[str] = []
            repeat_block_count = 0
            repair_nudges = 0
            last_failed_install_signature: str | None = None
            for _ in range(30):
                print("本地模型正在分析/决定下一步，请稍候……", flush=True)
                result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
                def request_model() -> None:
                    try:
                        result_queue.put((True, ollama_chat(opts.host, opts.model, messages, opts.timeout)))
                    except Exception as exc:
                        result_queue.put((False, exc))
                model_thread = threading.Thread(target=request_model, daemon=True)
                model_thread.start()
                try:
                    model_thread.join()
                except KeyboardInterrupt:
                    if model_thread.is_alive():
                        log_event(session_id, "pause", "模型生成期间用户请求暂停")
                        updated = pause_and_steer(messages, persist, session_id)
                        # Let the current generation finish, but discard its stale decision.
                        model_thread.join()
                        try:
                            result_queue.get_nowait()
                        except queue.Empty:
                            pass
                        messages.append({"role": "user", "content": "上一轮生成已因暂停作废。请根据最新方向重新分析，只输出一个 JSON 对象。"})
                        if updated:
                            messages.append({"role": "user", "content": f"[继续执行，采用新方向] {updated}"})
                        else:
                            print("已准备按新方向重开当前任务。")
                        persist()
                        continue
                    raise
                succeeded, result = result_queue.get()
                if not succeeded:
                    raise result
                message = result.get("message", {})
                raw_content = message.get("content", "")
                messages.append({"role": "assistant", "content": raw_content})
                persist()
                log_event(session_id, "model_output", raw_content)
                decisions = parse_decisions(raw_content)
                decision = normalize_model_action(decisions[0]) if decisions else None
                action = decision.get("action") if decision else None
                if decisions and decision.get("action") != decisions[0].get("action"):
                    print(f"[参数兼容] 已将 {decisions[0].get('action')} 转换为标准命令动作。", flush=True)

                if decisions and len(decisions) > 1:
                    # Execute only the first action in this decision turn. Any
                    # remaining objects are retained as assistant context, while
                    # the next model turn will be prompted for one action.
                    messages.append({"role": "user", "content": "你的上一条回复包含多个动作对象。系统将逐个处理其中的动作；现在请基于最新操作结果继续，每次只输出一个 JSON 对象。"})

                if mode == "ask" and action in TOOL_ACTIONS:
                    log_event(session_id, "skipped", f"提问模式下阻止工具动作：{action}")
                    print(f"[跳过] 当前为提问模式，未执行 {action}。")
                    messages.append({"role": "user", "content": "当前处于提问模式。请解释你的建议或计划，不要调用工具。"})
                    persist()
                    continue

                if action == "plan":
                    steps = decision.get("steps", [])
                    if not isinstance(steps, list):
                        steps = []
                    plan_shown = True
                    log_event(session_id, "plan", json.dumps({"steps": steps, "rationale": decision.get("rationale", "")}, ensure_ascii=False))
                    print("\n执行计划：", flush=True)
                    for number, step in enumerate(steps, start=1):
                        print(f"  {number}. {step}", flush=True)
                    if decision.get("rationale"):
                        print(f"  理由：{decision['rationale']}", flush=True)
                    messages.append({"role": "user", "content": "计划已记录。现在开始自主执行，逐项使用工具；每次只提出一个操作，并根据操作结果继续，直到完成或遇到无法解决的问题。"})
                    persist()
                    continue

                if action == "reply":
                    unfinished_write = requires_write and not written_files
                    unfinished_install = requires_install and "npm install" not in completed_commands
                    unfinished_build = requires_build and "npm run build" not in completed_commands
                    unfinished = requires_work and (not operation_log or unfinished_write or unfinished_install or unfinished_build)
                    clarification = is_clarification(decision.get("content", ""))
                    if pending_command_failure and repair_nudges < 3:
                        repair_nudges += 1
                        print(f"[自动诊断] 上次操作失败，要求先查原因、修复并回归验证（第 {repair_nudges}/3 轮）", flush=True)
                        issue = pending_issue or {"issue": "tool_execution_failure", "action": "run_command", "args": {}}
                        lesson = next((item for item in load_experiences() if item.get("issue") == issue["issue"]), None)
                        messages.append({"role": "user", "content": failure_recovery_prompt(issue["issue"], issue["action"], issue["args"], pending_command_failure, lesson)})
                        persist()
                        continue
                    if unfinished and not clarification and no_action_nudges < 3:
                        no_action_nudges += 1
                        print(f"[执行提醒] 请求尚未完成，要求模型继续操作（第 {no_action_nudges}/3 次）", flush=True)
                        pending = []
                        if not operation_log:
                            pending.append("先调用 list_files 检查工作区并开始实际操作")
                        if unfinished_write:
                            pending.append("至少实际写入一个符合需求的项目文件")
                        if unfinished_install:
                            pending.append("执行并确认 npm install 成功")
                        if unfinished_build:
                            pending.append("执行 npm run build 并确认构建成功")
                        messages.append({"role": "user", "content": "你还没有完成请求的操作：" + "；".join(pending) + "。请使用工具继续，不要只回复确认语或进度说明。"})
                        persist()
                        continue
                    if unfinished and not clarification:
                        print("\nAgent> 本轮未达到任务完成条件，已停止汇报为完成。请查看执行记录并继续发起任务。")
                        break
                    print(f"\nAgent> {decision.get('content', '').strip()}")
                    if operation_log:
                        print("\n本轮执行记录：")
                        for entry in operation_log:
                            print(f"  - {entry}")
                    elif plan_shown:
                        print("\n本轮没有执行任何工具操作。")
                    break

                if action not in TOOL_ACTIONS:
                    if not decision:
                        if invalid_json_retries >= 2:
                            print("\nAgent> 模型连续两次未能返回可执行的 JSON。原始回复已保存在会话历史中；本轮停止重试以避免重复消耗。你可以直接补充要求，或用 /new 开始新对话。")
                            persist()
                            break
                        invalid_json_retries += 1
                        print(f"[格式提醒] 回复未解析为动作 JSON（第 {invalid_json_retries} 次）。模型原文：{raw_content[:800]}", flush=True)
                        log_event(session_id, "parse_error", raw_content)
                        messages.append({"role": "user", "content": "上一条回复未能解析成动作。请不要重复解释或确认，直接把下一步改写成且只输出一个合法 JSON 对象；严禁连续输出多个 JSON。开发任务可用 {\"action\":\"plan\",\"steps\":[...]}，工具调用用 {\"action\":\"工具名\",\"args\":{...}}，完成时用 {\"action\":\"reply\",\"content\":\"...\"}。保留原任务，不要重新开始。"})
                        persist()
                        continue
                    unfinished = requires_work and (
                        not operation_log
                        or (requires_write and not written_files)
                        or (requires_install and "npm install" not in completed_commands)
                        or (requires_build and "npm run build" not in completed_commands)
                    )
                    if unfinished and no_action_nudges < 3:
                        no_action_nudges += 1
                        print(f"[执行提醒] 模型返回了非工具动作，尚未开始工作（第 {no_action_nudges}/3 次）", flush=True)
                        messages.append({"role": "user", "content": "这个开发请求尚未执行。你返回的 JSON action 不是可执行工具。请立即返回 action=plan，随后使用 list_files、read_file、write_file、modify_file、set_project_goal、create_directory、move_file、change_directory 或白名单内的 run_command 实际操作；不要输出确认语。"})
                        persist()
                        continue
                    if unfinished:
                        print("\nAgent> 模型未按工具协议继续，任务尚未完成；没有将确认语当作完成结果。")
                        break
                    project_state = update_project_state(
                        workspace, project_state, current_directory=current_directory,
                        goal_achieved=not clarification,
                    )
                    print(f"\nAgent> {decision.get('content', raw_content).strip()}")
                    break

                args = normalize_tool_args(decision)
                # Some local models emit the command beside `action` instead
                # of inside `args`. Normalize this common schema variation.
                original_args = decision.get("args")
                if action == "run_command" and not (isinstance(original_args, dict) and "command" in original_args) and isinstance(decision.get("command"), str):
                    log_event(session_id, "argument_normalization", "已将顶层 command 归入 args.command")
                    print("[参数兼容] 已将顶层 command 转换为 args.command。", flush=True)
                if not plan_shown:
                    print("\n执行计划：模型未先给出计划，按当前需求直接执行并在完成后汇报。", flush=True)
                    plan_shown = True
                print(f"[操作] {action}", flush=True)
                rationale = decision.get("rationale", "未提供")
                print(f"[行动理由] {rationale}", flush=True)
                log_event(session_id, "action", f"{action}: {rationale}; args={json.dumps(args, ensure_ascii=False)}")
                is_npm_install = action == "run_command" and bool(re.fullmatch(r"npm install(?:\s+.*)?", str(args.get("command", ""))))
                install_signature = dependency_signature(current_directory) if is_npm_install else None
                repeat_unmodified = should_block_repeat(pending_issue, recovery_actions, action, args)
                if repeat_unmodified:
                    repeat_block_count += 1
                    output = "已阻止重复失败操作：失败后尚未进行任何修复或状态变更。请先检查证据并采取不同的诊断/修复动作。"
                elif is_npm_install and last_failed_install_signature == install_signature:
                    output = "已阻止重复安装：上一次 npm install 失败后 package.json 和 package-lock.json 都没有变化。请先修复依赖配置；若错误是网络或环境问题，可在修复后重新运行。ETARGET 表示请求的包版本不存在，不要清理缓存或重复安装。"
                else:
                    output = run_tool(workspace, action, args, current_directory)
                if action == "change_directory" and output.startswith("当前工作目录已切换为："):
                    current_directory = path_in_workspace(workspace, args.get("path", "."), current_directory)
                    print(f"[当前目录] {current_directory.relative_to(workspace).as_posix() or '.'}", flush=True)
                if action in {"write_file", "modify_file"}:
                    operation_log.append(f"{'文件写入' if action == 'write_file' else '文件修改'}：{args.get('path', '')}")
                    print(f"[操作结果] {output}", flush=True)
                    if output.startswith(("已写入：", "已修改：")):
                        written_files.add(str(args.get("path", "")))
                elif action == "set_project_goal":
                    operation_log.append("设置项目目标")
                    print(f"[操作结果] {output}", flush=True)
                elif action == "create_directory":
                    operation_log.append(f"创建目录：{args.get('path', '')}")
                    print(f"[操作结果] {output}", flush=True)
                elif action == "move_file":
                    operation_log.append(f"移动文件：{args.get('source', '')} → {args.get('destination', '')}")
                    print(f"[操作结果] {output}", flush=True)
                elif action == "change_directory":
                    operation_log.append(f"切换工作目录：{args.get('path', '')}")
                    print(f"[操作结果] {output}", flush=True)
                elif action == "run_command":
                    operation_log.append(f"命令：{args.get('command', '')}")
                    print(f"[操作结果] {output}", flush=True)
                    if output.startswith("退出码：0"):
                        completed_commands.add(args.get("command", ""))
                    elif output.startswith("开发服务器进程已启动"):
                        completed_commands.add(args.get("command", ""))
                    is_audit_report = args.get("command") == "npm audit"
                    if output.startswith("退出码：") and not output.startswith("退出码：0") and not is_audit_report:
                        pending_command_failure = output
                        if is_npm_install:
                            last_failed_install_signature = dependency_signature(current_directory)
                        if "ETARGET" in output or "No matching version found" in output:
                            repair = auto_repair_known_npm_error(current_directory, output)
                            if repair:
                                output += "\n自动诊断与修复：" + repair
                                print(f"[自动修复] {repair}", flush=True)
                                if is_npm_install and "已自动修正 package.json" in repair:
                                    print("[自动修复] 配置已改变，立即重试 npm install。", flush=True)
                                    retry_output = run_tool(workspace, "run_command", {"command": "npm install"}, current_directory)
                                    print(f"[重试结果] {retry_output}", flush=True)
                                    output += "\n自动重试结果：\n" + retry_output
                                    if retry_output.startswith("退出码：0"):
                                        completed_commands.add("npm install")
                                        pending_command_failure = None
                                    else:
                                        pending_command_failure = retry_output
                                        last_failed_install_signature = dependency_signature(current_directory)
                            else:
                                output += "\n诊断：npm 找不到 package.json 请求的版本。请读取 package.json，将该依赖改为真实存在且相互兼容的版本后再安装；该问题不是 npm 缓存导致的。"
                    elif output.startswith("退出码：0") or output.startswith("开发服务器进程已启动"):
                        pending_command_failure = None
                    elif is_audit_report:
                        pending_command_failure = None
                else:
                    operation_log.append(action)
                project_state = update_project_state(
                    workspace, project_state, action, args, output, rationale,
                    current_directory=current_directory,
                    project_goal=args.get("goal") if action == "set_project_goal" and output == "项目目标已更新。" else None,
                )
                state_context = format_project_state(project_state)
                print(project_state_console_summary(project_state), flush=True)
                log_event(session_id, "project_state_update", state_context)
                messages.append({"role": "user", "content": state_context + "\n请先依据这些文件事实和命令结果更新判断，再选择下一步；下一动作 rationale 简短说明证据和理由。命令成功不代表项目配置完整。"})
                messages.append({"role": "user", "content": f"操作结果（{action}）：\n{output}\n请根据结果继续；每次仍只输出 JSON 对象。"})
                log_event(session_id, "tool_result", f"{action}: {output}")
                issue_kind = classify_tool_failure(action, args, output)
                if repeat_unmodified:
                    issue = pending_issue or {"issue": "tool_execution_failure", "action": action, "args": args, "output": output}
                    known = next((item for item in load_experiences() if item.get("issue") == issue["issue"]), None)
                    messages.append({"role": "user", "content": failure_recovery_prompt(issue["issue"], issue["action"], issue["args"], output, known)})
                    log_event(session_id, "repeat_blocked", output)
                    print("[重复操作保护] 状态未改变，已拦截相同操作。", flush=True)
                elif issue_kind:
                    repeat_block_count = 0
                    pending_issue = {"issue": issue_kind, "action": action, "args": dict(args), "output": output}
                    recovery_actions = []
                    pending_command_failure = output
                    known = next((item for item in load_experiences() if item.get("issue") == issue_kind), None)
                    recovery_prompt = failure_recovery_prompt(issue_kind, action, args, output, known)
                    messages.append({"role": "user", "content": recovery_prompt})
                    print(f"[问题诊断] {issue_kind}；已要求检查证据、修复并回归验证。", flush=True)
                    log_event(session_id, "failure_detected", recovery_prompt)
                elif pending_issue and tool_succeeded(action, args, output):
                    repeat_block_count = 0
                    if action in {"write_file", "modify_file"}:
                        recovery_actions.append(f"{'写入' if action == 'write_file' else '修改'} {args.get('path', '')}")
                    elif action == "create_directory":
                        recovery_actions.append(f"创建目录 {args.get('path', '')}")
                    elif action == "move_file":
                        recovery_actions.append(f"移动文件 {args.get('source', '')} → {args.get('destination', '')}")
                    elif action == "run_command":
                        recovery_actions.append(f"执行 {args.get('command', '')}")
                    if verification_succeeded(pending_issue["issue"], pending_issue["action"], pending_issue["args"], action, args, output):
                        repair = "；".join(recovery_actions) or "改用正确工具/参数完成检查"
                        if action == "read_file":
                            proof = f"read_file 成功读取目标文件（返回 {len(output)} 个字符）。"
                        elif action == "run_command":
                            proof = f"{args.get('command', '命令')} 返回成功标记；后台启动失败检查通过。"
                        else:
                            proof = f"{action} 对原失败条件完成回归验证。"
                        entry = {"issue": pending_issue["issue"], "lesson": f"{failure_lesson(pending_issue['issue'])} 已采用：{repair}。", "proof": proof, "failed_action": pending_issue["action"], "repair": recovery_actions}
                        record_verified_experience(entry)
                        log_event(session_id, "issue_resolved", json.dumps(entry, ensure_ascii=False))
                        print(f"[回归验证] {pending_issue['issue']} 已通过；经验已保存供后续复用。", flush=True)
                        pending_issue = None
                        pending_command_failure = None
                persist()
                if repeat_block_count >= 3:
                    print("Agent> 相同失败动作在未修复时重复提出三次，已停止本轮；请查看诊断记录并调整方向。", flush=True)
                    log_event(session_id, "recovery_stopped", "同一失败动作被重复提出三次，状态未改变")
                    break
            else:
                print("Agent> 本轮达到 30 个决策步骤上限。已完成的修改已保留，请继续描述剩余任务。")
        except (KeyboardInterrupt, EOFError):
            print("\n已退出。")
            return 0
        except Exception as exc:
            print(f"\n错误：{exc}")
            persist()


if __name__ == "__main__":
    sys.exit(main())
