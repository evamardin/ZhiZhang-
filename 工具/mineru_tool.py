#!/usr/bin/env python3
"""MinerU 文档解析工具 (CLI)

用法:
    # 精准解析（需要配置 token）
    python mineru_tool.py parse url     <文件URL>                      [选项]
    python mineru_tool.py parse file    <本地文件路径>                  [选项]

    # Agent 轻量解析（无需 token）
    python mineru_tool.py agent url     <文件URL>                      [选项]
    python mineru_tool.py agent file    <本地文件路径>                  [选项]

    # 配置 token
    python mineru_tool.py config --token "你的token"

选项:
    --model      模型版本: pipeline / vlm / MinerU-HTML  (默认 vlm)
    --lang       语言代码 (默认 ch)
    --ocr        启用 OCR
    --no-table   关闭表格识别
    --no-formula 关闭公式识别
    --page-ranges 页码范围，如 "2,4-6"
    --extra      额外导出格式: docx,html,latex
    --timeout    超时秒数 (默认 300)
    -o, --out    输出目录 (默认 解析结果/)
"""

import argparse
import json
import os
import sys
import time
import zipfile
import io
from pathlib import Path

import requests


BASE_DIR = Path(__file__).parent
DEFAULT_CONFIG = BASE_DIR / "mineru_config.json"
DEFAULT_OUTPUT = BASE_DIR.parent / "解析结果"


def load_config():
    if not DEFAULT_CONFIG.exists():
        return {
            "token": "",
            "base_url": "https://mineru.net",
            "default_model": "vlm",
            "default_language": "ch",
            "enable_table": True,
            "enable_formula": True,
            "is_ocr": False,
            "output_dir": str(DEFAULT_OUTPUT),
            "timeout": 300,
            "poll_interval": 3,
        }
    with open(DEFAULT_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    DEFAULT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(DEFAULT_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    print(f"配置已保存到 {DEFAULT_CONFIG}")


# ── 精准解析 API ─────────────────────────────────────────────────


def _headers(cfg):
    token = cfg.get("token", "").strip()
    if not token:
        print("错误: 未配置 token。请执行: python mineru_tool.py config --token '你的token'")
        sys.exit(1)
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def build_task_args(cfg, ns):
    """从配置 + cli_args 组装请求体公共字段。"""
    args = {
        "model_version": ns.model or cfg["default_model"],
        "language": ns.lang or cfg["default_language"],
        "is_ocr": ns.ocr if ns.ocr is not None else cfg["is_ocr"],
        "enable_table": ns.enable_table if ns.enable_table is not None else cfg["enable_table"],
        "enable_formula": ns.enable_formula if ns.enable_formula is not None else cfg["enable_formula"],
    }
    if ns.extra:
        args["extra_formats"] = ns.extra.split(",")
    if ns.page_ranges:
        args["page_ranges"] = ns.page_ranges
    if ns.data_id:
        args["data_id"] = ns.data_id
    return args


def parse_precision_url(url, cfg, ns):
    """精准解析 - URL 提交（单文件）"""
    api = f'{cfg["base_url"]}/api/v4/extract/task'
    data = build_task_args(cfg, ns)
    data["url"] = url
    resp = requests.post(api, headers=_headers(cfg), json=data)
    result = resp.json()
    if result["code"] != 0:
        print(f"提交失败: {result['msg']}")
        return None
    task_id = result["data"]["task_id"]
    print(f"任务已提交, task_id: {task_id}")
    return task_id


def parse_precision_file(file_path, cfg, ns):
    """精准解析 - 本地文件上传"""
    api = f'{cfg["base_url"]}/api/v4/file-urls/batch'
    file_name = Path(file_path).name
    data = build_task_args(cfg, ns)
    data["files"] = [{"name": file_name, "data_id": ns.data_id or "file"}]
    resp = requests.post(api, headers=_headers(cfg), json=data)
    result = resp.json()
    if result["code"] != 0:
        print(f"申请上传链接失败: {result['msg']}")
        return None
    batch_id = result["data"]["batch_id"]
    upload_url = result["data"]["file_urls"][0]
    print(f"batch_id: {batch_id}")
    # 上传文件
    with open(file_path, "rb") as f:
        put_resp = requests.put(upload_url, data=f)
    if put_resp.status_code not in (200, 201):
        print(f"文件上传失败, HTTP {put_resp.status_code}")
        return None
    print("文件上传成功，等待解析...")
    return batch_id  # 注意: batch 查询用 batch_id


def poll_precision_result(task_id, cfg, ns, output_dir):
    """轮询单文件精准解析结果"""
    timeout = ns.timeout or cfg["timeout"]
    interval = cfg.get("poll_interval", 3)
    api = f'{cfg["base_url"]}/api/v4/extract/task/{task_id}'
    headers = _headers(cfg)
    start = time.time()

    while time.time() - start < timeout:
        resp = requests.get(api, headers=headers)
        result = resp.json()
        state = result["data"]["state"]
        elapsed = int(time.time() - start)

        if state == "done":
            zip_url = result["data"]["full_zip_url"]
            print(f"[{elapsed}s] 解析完成 → 下载结果...")
            return _download_and_extract_zip(zip_url, output_dir)
        if state == "failed":
            print(f"[{elapsed}s] 解析失败: {result['data'].get('err_msg', '未知错误')}")
            return None
        label = {"pending": "排队中", "running": "解析中", "converting": "格式转换中"}
        print(f"[{elapsed}s] {label.get(state, state)}...")
        time.sleep(interval)

    print(f"轮询超时 ({timeout}s)")
    return None


def poll_precision_batch(batch_id, cfg, ns, output_dir):
    """轮询批量精准解析结果"""
    timeout = ns.timeout or cfg["timeout"]
    interval = cfg.get("poll_interval", 3)
    api = f'{cfg["base_url"]}/api/v4/extract-results/batch/{batch_id}'
    headers = _headers(cfg)
    start = time.time()

    while time.time() - start < timeout:
        resp = requests.get(api, headers=headers)
        result = resp.json()
        if result["code"] != 0:
            print(f"查询失败: {result['msg']}")
            time.sleep(interval)
            continue
        results = result["data"]["extract_result"]
        all_done = all(r["state"] == "done" or r["state"] == "failed" for r in results)
        elapsed = int(time.time() - start)
        for r in results:
            fname = r.get("file_name", "?")
            st = r["state"]
            if st == "done":
                print(f"  [{elapsed}s] {fname} 完成")
            elif st == "failed":
                print(f"  [{elapsed}s] {fname} 失败: {r.get('err_msg', '')}")
            else:
                print(f"  [{elapsed}s] {fname} {st}...")
        if all_done:
            # 下载完成的文件
            for r in results:
                if r["state"] == "done" and r.get("full_zip_url"):
                    _download_and_extract_zip(r["full_zip_url"], output_dir, prefix=r.get("file_name", ""))
            return
        time.sleep(interval)

    print(f"轮询超时 ({timeout}s)")


def _download_and_extract_zip(zip_url, output_dir, prefix=""):
    """下载 zip 并解压到输出目录"""
    resp = requests.get(zip_url, stream=True)
    if resp.status_code != 200:
        print(f"下载结果失败, HTTP {resp.status_code}")
        return None
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    sub_dir = prefix.replace(".", "_") if prefix else "result"
    extract_path = Path(output_dir) / sub_dir
    extract_path.mkdir(parents=True, exist_ok=True)
    z.extractall(extract_path)
    # 列出主要文件
    out_files = []
    for name in z.namelist():
        if not name.endswith("/"):
            full_path = extract_path / name
            out_files.append(str(full_path))
    print(f"  结果已保存到: {extract_path}/")
    for fp in out_files:
        print(f"    {fp}")
    return str(extract_path)


# ── Agent 轻量 API ────────────────────────────────────────────────


def agent_parse_url(url, cfg, ns):
    """Agent API - URL 提交"""
    api = f'{cfg["base_url"]}/api/v1/agent/parse/url'
    data = {
        "url": url,
        "language": ns.lang or cfg["default_language"],
        "enable_table": ns.enable_table if ns.enable_table is not None else cfg["enable_table"],
        "is_ocr": ns.ocr if ns.ocr is not None else cfg["is_ocr"],
        "enable_formula": ns.enable_formula if ns.enable_formula is not None else cfg["enable_formula"],
    }
    if ns.page_ranges:
        data["page_range"] = ns.page_ranges
    resp = requests.post(api, json=data)
    result = resp.json()
    if result["code"] != 0:
        print(f"提交失败: {result['msg']}")
        return None
    task_id = result["data"]["task_id"]
    print(f"任务已提交, task_id: {task_id}")
    return task_id


def agent_parse_file(file_path, cfg, ns):
    """Agent API - 本地文件签名上传"""
    api = f'{cfg["base_url"]}/api/v1/agent/parse/file'
    file_name = Path(file_path).name
    data = {
        "file_name": file_name,
        "language": ns.lang or cfg["default_language"],
        "enable_table": ns.enable_table if ns.enable_table is not None else cfg["enable_table"],
        "is_ocr": ns.ocr if ns.ocr is not None else cfg["is_ocr"],
        "enable_formula": ns.enable_formula if ns.enable_formula is not None else cfg["enable_formula"],
    }
    if ns.page_ranges:
        data["page_range"] = ns.page_ranges
    resp = requests.post(api, json=data)
    result = resp.json()
    if result["code"] != 0:
        print(f"获取上传链接失败: {result['msg']}")
        return None
    task_id = result["data"]["task_id"]
    file_url = result["data"]["file_url"]
    print(f"task_id: {task_id}")
    with open(file_path, "rb") as f:
        put_resp = requests.put(file_url, data=f)
    if put_resp.status_code not in (200, 201):
        print(f"文件上传失败, HTTP {put_resp.status_code}")
        return None
    print("文件上传成功，等待解析...")
    return task_id


def agent_poll_result(task_id, cfg, ns, output_dir):
    """轮询 Agent API 解析结果"""
    timeout = ns.timeout or cfg["timeout"]
    interval = cfg.get("poll_interval", 3)
    api = f'{cfg["base_url"]}/api/v1/agent/parse/{task_id}'
    start = time.time()

    while time.time() - start < timeout:
        resp = requests.get(api)
        result = resp.json()
        if result["code"] != 0:
            print(f"查询异常: {result}")
            time.sleep(interval)
            continue
        state = result["data"]["state"]
        elapsed = int(time.time() - start)

        if state == "done":
            md_url = result["data"]["markdown_url"]
            print(f"[{elapsed}s] 解析完成 → 下载 Markdown...")
            return _download_markdown(md_url, task_id, output_dir)
        if state == "failed":
            print(f"[{elapsed}s] 解析失败: {result['data'].get('err_msg', '未知错误')}")
            return None
        label = {
            "waiting-file": "等待文件上传",
            "uploading": "文件下载中",
            "pending": "排队中",
            "running": "解析中",
        }
        print(f"[{elapsed}s] {label.get(state, state)}...")
        time.sleep(interval)

    print(f"轮询超时 ({timeout}s)")
    return None


def _download_markdown(md_url, task_id, output_dir):
    """从 CDN 下载 Markdown 并保存"""
    resp = requests.get(md_url)
    if resp.status_code != 200:
        print(f"下载 Markdown 失败, HTTP {resp.status_code}")
        return None
    ext = Path(md_url).suffix or ".md"
    out_path = Path(output_dir) / f"{task_id}{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(resp.text, encoding="utf-8")
    print(f"  结果已保存到: {out_path}")
    return str(out_path)


# ── CLI ────────────────────────────────────────────────────────────


def cmd_config(args, cfg, _output_dir):
    cfg["token"] = args.token
    save_config(cfg)
    # 同时写 .env 方便其他脚本引用
    env_path = Path(__file__).parent / ".env"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f'MINERU_TOKEN={args.token}\n')
    print(f"Token 已配置完成（末尾 {args.token[-8:]}）")

    # 测试 token 有效
    test_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {args.token}",
    }
    test_url = f'{cfg["base_url"]}/api/v4/extract/task'
    test_data = {
        "url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
        "model_version": "vlm",
    }
    try:
        r = requests.post(test_url, headers=test_headers, json=test_data)
        if r.status_code == 200:
            print("Token 验证通过 ✓")
        else:
            print(f"Token 验证失败 (HTTP {r.status_code}): {r.text[:200]}")
    except Exception as e:
        print(f"Token 验证异常: {e}")


def cmd_parse(args, cfg, output_dir):
    sub = args.subcommand
    if sub == "url":
        task_id = parse_precision_url(args.path_or_url, cfg, args)
    elif sub == "file":
        task_id = parse_precision_file(os.path.abspath(args.path_or_url), cfg, args)
    else:
        print(f"未知子命令: {sub}")
        sys.exit(1)

    if task_id is None:
        return
    print(f"\n轮询任务结果...")
    if sub == "file":
        # 文件上传返回 batch_id, 需用 batch 轮询
        poll_precision_batch(task_id, cfg, args, output_dir)
    else:
        # URL 提交返回 task_id, 用 task 轮询
        poll_precision_result(task_id, cfg, args, output_dir)


def cmd_agent(args, cfg, output_dir):
    sub = args.subcommand
    if sub == "url":
        task_id = agent_parse_url(args.path_or_url, cfg, args)
    elif sub == "file":
        task_id = agent_parse_file(os.path.abspath(args.path_or_url), cfg, args)
    else:
        print(f"未知子命令: {sub}")
        sys.exit(1)

    if task_id is None:
        return
    print(f"\n轮询任务结果...")
    agent_poll_result(task_id, cfg, args, output_dir)


def get_parser():
    p = argparse.ArgumentParser(description="MinerU 文档解析工具")
    p.add_argument("--out", "-o", default=None, help="输出目录 (默认 解析结果/)")

    sub = p.add_subparsers(dest="mode", required=True)

    # config
    cp = sub.add_parser("config", help="配置 API Token")
    cp.add_argument("--token", required=True, help="MinerU API Token")

    # parse (精准解析)
    pp = sub.add_parser("parse", help="精准解析 API（需要 Token）")
    pp.add_argument("subcommand", choices=["url", "file"])
    pp.add_argument("path_or_url", help="文件 URL 或本地路径")
    _add_common_args(pp)

    # agent (轻量解析)
    ap = sub.add_parser("agent", help="Agent 轻量解析 API（无需 Token）")
    ap.add_argument("subcommand", choices=["url", "file"])
    ap.add_argument("path_or_url", help="文件 URL 或本地路径")
    _add_common_args(ap)

    return p


def _add_common_args(sp):
    sp.add_argument("--timeout", type=int, default=None, help="超时秒数")
    sp.add_argument("--model", choices=["pipeline", "vlm", "MinerU-HTML"])
    sp.add_argument("--lang", help="文档语言 (默认 ch)")
    sp.add_argument("--ocr", action="store_true", default=None)
    sp.add_argument("--no-table", dest="enable_table", action="store_false", default=None)
    sp.add_argument("--no-formula", dest="enable_formula", action="store_false", default=None)
    sp.add_argument("--page-ranges", help='页码范围, 如 "2,4-6"')
    sp.add_argument("--extra", help='额外导出格式, 如 "docx,html"')
    sp.add_argument("--data-id", help="自定义数据 ID")


def main():
    cfg = load_config()
    parser = get_parser()
    args = parser.parse_args()

    output_dir = args.out or cfg.get("output_dir") or str(DEFAULT_OUTPUT)

    if args.mode == "config":
        cmd_config(args, cfg, output_dir)
    elif args.mode == "parse":
        cmd_parse(args, cfg, output_dir)
    elif args.mode == "agent":
        cmd_agent(args, cfg, output_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
