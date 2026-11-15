# -*- coding: utf-8 -*-
"""把汇总结果打成 q-gallery 分片 JSON，直推 Cloudflare R2（替代 Apps Script → Drive）。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fetch_posts import load_sync_state, remember_cf_fingerprint, save_sync_state, to_datetime, with_retry

LogFn = Callable[[str], None]

# Only the actual spreadsheet header names define columns. No positional fallback.
HEADER_TO_ASSET = {
    "管理员": "name", "语言": "language", "专页id": "maker", "专页名字": "pageName",
    "帖文链接": "postLink", "引流": "lead", "帖文ID": "postId", "头像": "avatarUrl",
    "图片链接": "imageUrl", "发帖日期": "date", "点赞": "likes", "评论": "comments",
    "分享": "shares", "帖文类型": "postType", "帖文内容": "content", "帖文翻译": "postTranslation",
    "文案类型": "copyType", "OCR": "ocr", "OCR翻译": "ocrTranslation",
    "图片类型": "category", "音频文本": "audioText", "音频文本翻译": "audioTranslation",
    "题材类型": "topicType", "来源类型": "sourceType", "缩略图": "thumbnailUrl",
    "交教会": "water", "交教会时间": "waterTime", "转换率": "conversionRate", "美工": "designer",
}
HEADER_ALIASES = {
    "水滴时间": "交教会时间",
    "水滴日期": "交教会时间",
    "交教会日期": "交教会时间",
}
URL_FIELDS = {"帖文链接", "图片链接", "缩略图", "头像"}
_NOISE_RE = re.compile(
    r"[\U0001F300-\U0010ffff]|[\u2600-\u27bf]|[\ufe0e\ufe0f]|[|·•🎨📜]"
)

_FORMULA_URL_RE = re.compile(
    r"""(?:HYPERLINK|IMAGE)\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s\"')>,]+", re.IGNORECASE)


def _norm_header(value: Any) -> str:
    from html import unescape
    text = unescape(str(value or "")).strip().lower()
    text = _NOISE_RE.sub("", text)
    return text.replace(" ", "").replace("_", "")


def _canonical_header(value: Any) -> str:
    key = _norm_header(value)
    direct = next((name for name in HEADER_TO_ASSET if _norm_header(name) == key), "")
    if direct:
        return direct
    alias = next((target for name, target in HEADER_ALIASES.items() if _norm_header(name) == key), "")
    return alias if alias in HEADER_TO_ASSET else ""


def _looks_like_header(row: list[Any] | None) -> bool:
    return bool(row and sum(bool(_canonical_header(cell)) for cell in row) >= 2)


def split_sheet_for_publish(
    values: list[list[Any]],
    start_row: int,
    include_headers: bool,
) -> tuple[list[str], list[list[Any]], str, int]:
    """起始行可能是表头，也可能直接是数据（结果表可能从第 3 行起、不写表头）。"""
    start = max(1, int(start_row or 1))
    if len(values) < start:
        raise RuntimeError("目标表没有数据可发布")
    head = values[start - 1]
    if _looks_like_header(head):
        headers = [str(x).strip() for x in head]
        return headers, values[start:], f"第 {start} 行表头", start + 1
    # Results may begin below an existing header, but never guess column positions.
    for i in range(start - 1):
        if _looks_like_header(values[i]):
            return [str(x).strip() for x in values[i]], values[start - 1:], f"第 {i + 1} 行表头", start
    raise ValueError("没有识别到表格表头，请填写正确的表头行；已停止发布，不再按旧列位置读取")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if value is None:
        return ""
    dt = to_datetime(value)
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return _text(value)


def extract_url_from_cell(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    m = _FORMULA_URL_RE.search(text)
    if m:
        return m.group(1).strip()
    if text.startswith("="):
        m = _URL_RE.search(text)
        return (m.group(0).rstrip(").,;]") if m else "").strip()
    if text.lower().startswith("http"):
        return text
    m = _URL_RE.search(text)
    return (m.group(0) if m else text).strip()


def drive_file_id(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    match = re.search(r"(?:drive|docs)\.google\.com/file/d/([^/?#]+)", text, re.I)
    if match:
        return match.group(1)
    match = re.search(r"lh3\.googleusercontent\.com/d/([a-zA-Z0-9_-]+)", text, re.I)
    if match:
        return match.group(1)
    if re.search(r"(?:drive|docs|usercontent)\.google\.com", text, re.I):
        match = re.search(r"[?&]id=([^&#]+)", text, re.I)
        if match:
            return match.group(1)
    return ""


def public_image_url(url: str) -> str:
    """Turn Drive file/view links into an embeddable image URL for <img> tags."""
    raw = extract_url_from_cell(url)
    file_id = drive_file_id(raw)
    if file_id:
        return f"https://lh3.googleusercontent.com/d/{file_id}"
    return raw


def _date_text(value: Any) -> str:
    dt = to_datetime(value)
    if not dt:
        text = _text(value)
        if text.isdigit():
            dt = to_datetime(int(text))
        elif len(text) >= 10 and text[4] in "-/":
            return text[:10].replace("/", "-")
    if dt:
        return dt.strftime("%Y-%m-%d")
    return _text(value)


def _cell(row: list[Any], idx: int | None) -> Any:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return row[idx]


def _url_hits(rows: list[list[Any]], idx: int, limit: int = 40) -> int:
    hits = 0
    for row in rows[:limit]:
        text = str(_cell(row, idx) or "").lower()
        if "http" in text or text.strip().startswith("="):
            hits += 1
    return hits


def _header_index(headers: list[str], rows: list[list[Any]]) -> dict[str, int]:
    index: dict[str, int] = {}
    for i, h in enumerate(headers or []):
        key = _canonical_header(h)
        if not key:
            continue
        if key not in index:
            index[key] = i
            continue
        raise ValueError(f"表头「{key}」重复，请保留唯一一列后再发布")
    return index


def _first_http(*values: Any) -> str:
    for value in values:
        url = extract_url_from_cell(value)
        if url.lower().startswith("http"):
            return url
    return ""


def rows_to_assets(
    headers: list[str],
    rows: list[list[Any]],
    group_values: list[Any] | None = None,
    log: LogFn | None = None,
    start_row: int = 1,
) -> list[dict[str, Any]]:
    index = _header_index(headers, rows)
    if "帖文链接" not in index:
        raise ValueError("缺少必需表头「帖文链接」，请检查表头行；旧图库未替换")
    assets = []
    for offset, row in enumerate(rows):
        asset = {field: _text(_cell(row, index.get(header))) for header, field in HEADER_TO_ASSET.items()}
        asset["waterTime"] = _timestamp_text(_cell(row, index.get("交教会时间")))
        link = _first_http(asset["postLink"])
        if not link:
            continue
        row_number = start_row + offset
        image = public_image_url(asset["imageUrl"])
        thumb = public_image_url(asset["thumbnailUrl"]) or image
        asset.update(
            # A sheet may contain multiple rows for one post. The gallery stores records
            # by ID, so an ID based only on post ID/link silently collapses those rows.
            id=hashlib.sha256(f"{asset['postId'] or link}|row:{row_number}".encode()).hexdigest()[:32],
            rowNumber=row_number, postLink=link, sourceUrl=link, viewUrl=link,
            title=asset["name"] or asset["pageName"] or asset["postType"] or "素材",
            date=_date_text(_cell(row,index.get("发帖日期"))),
            imageUrl=image, previewUrl=thumb, thumbnailUrl=thumb, thumbnailFallbackUrl=image or thumb,
            avatarUrl=_first_http(asset["avatarUrl"]), authorName=asset["maker"],
            type=asset["category"] or asset["postType"],
            group=_text(group_values[offset]) if group_values is not None and offset<len(group_values) else "",
            mediaMode="video" if "视频" in asset["postType"]+asset["category"] else "image",
        )
        assets.append(asset)
    if log:
        log(f"按实际表头读取：有效 {len(assets)} 条，跳过 {len(rows)-len(assets)} 条无帖文链接记录")
        image_categories = sum(bool(asset.get("category")) for asset in assets)
        topic_categories = sum(bool(asset.get("topicType")) for asset in assets)
        log(f"分类读取：T列图片类型 {image_categories} 条，W列题材类型 {topic_categories} 条")
    return assets


def overlay_formula_urls(ws, rows, start_row, log=print, *, headers=None):
    """Read formula URLs only from columns identified by the actual headers."""
    from fetch_posts import index_to_col_letter
    if not rows: return
    index = _header_index(headers or [],rows)
    cols = sorted(index[name] for name in URL_FIELDS if name in index)
    if not cols: return
    end = start_row + len(rows)-1
    ranges = [f"{index_to_col_letter(c)}{start_row}:{index_to_col_letter(c)}{end}" for c in cols]
    batches = with_retry(lambda: ws.batch_get(ranges,value_render_option="FORMULA"), log=log,what="读取链接公式")
    for values, col in zip(batches,cols):
        for offset, cells in enumerate(values):
            if offset >= len(rows): break
            url = _first_http(cells[0] if cells else "")
            if url:
                while len(rows[offset]) <= col: rows[offset].append("")
                rows[offset][col] = url


def mapped_post_headers(headers, mappings=None):
    """Rename explicitly configured source headers without positional assumptions."""
    result = [str(h).strip() for h in headers]
    for item in mappings or []:
        source, target = str(item.get('source') or '').strip(), str(item.get('target') or '').strip()
        if not source or not target:
            raise ValueError('字段映射的源表头和目标字段不能为空')
        matches = [i for i, h in enumerate(headers) if _norm_header(h) == _norm_header(source)]
        if len(matches) != 1:
            raise ValueError(f'映射源表头「{source}」不存在或重复，请重新读取表头')
        result[matches[0]] = target
    names = [_norm_header(h) for h in result if _norm_header(h)]
    if len(names) != len(set(names)):
        raise ValueError('映射后的表头字段名称重复，请检查')
    return result


def filter_source_settings(cfg):
    from fetch_posts import normalize_sources
    sources = normalize_sources(getattr(cfg, "sources", None) or getattr(cfg, "source_urls", None))
    if sources:
        return [src.url for src in sources], cfg.filter_source_sheet, cfg.filter_header_row
    # Compatibility for saved configurations from the separate direct-publish form.
    return ([cfg.cf_direct_url] if cfg.cf_direct_url else []), cfg.cf_direct_sheet, cfg.cf_direct_header_row


def peek_filter_headers(cfg):
    from fetch_posts import authorize_cfg, open_by_url_or_id, with_retry
    urls, sheet, top = filter_source_settings(cfg)
    if not urls: raise ValueError('请先填写数据源表格链接')
    top = int(top)
    if top < 1: raise ValueError('表头行必须是正整数')
    ss = open_by_url_or_id(authorize_cfg(cfg), urls[0])
    ws = post_source_worksheet(ss, urls[0], sheet)
    headers = with_retry(lambda: ws.row_values(top), what='读取表头')
    mapped_post_headers(headers)
    return [{'source': str(h).strip(), 'target': _canonical_header(h) or str(h).strip()}
            for h in headers if _norm_header(h)]


def post_source_worksheet(ss, url, sheet_name):
    from urllib.parse import urlsplit, parse_qs
    if sheet_name:
        ws = ss.worksheet(sheet_name)
    else:
        parsed = urlsplit(url)
        gid = (parse_qs(parsed.fragment).get('gid') or parse_qs(parsed.query).get('gid') or [None])[0]
        if gid is not None:
            ws = ss.get_worksheet_by_id(int(gid))
        else:
            tabs = [tab for tab in ss.worksheets() if not (getattr(tab, '_properties', {}) or {}).get('hidden')]
            if len(tabs) != 1: raise ValueError('请填写源子表名称，或使用带 gid 的子表链接')
            ws = tabs[0]
    if ws is None: raise ValueError('没有找到指定子表')
    return ws


def read_post_source(ss, url, sheet_name, header_row, log=print, *, mappings=None):
    """Return all named source columns in their actual order, without field mappings."""
    from fetch_posts import read_sheet_values
    from urllib.parse import urlsplit, parse_qs
    from sheet_links import read_links
    from metric_sheet_io import MetricReader
    try: top=int(header_row)
    except (ValueError,TypeError): raise ValueError("表头行必须是正整数")
    if top<1: raise ValueError("表头行必须是正整数")
    ws = post_source_worksheet(ss, url, sheet_name)
    values=read_sheet_values(ws,log=log)
    if len(values)<top: raise ValueError("表头行超出表格数据范围")
    headers=mapped_post_headers(values[top-1], mappings)
    positions=[i for i,h in enumerate(headers) if _norm_header(h)]
    names=[_norm_header(headers[i]) for i in positions]
    if not names or len(names)!=len(set(names)): raise ValueError("源表表头为空或重复，请检查")
    rows=[list(row) for row in values[top:]]
    overlay_formula_urls(ws,rows,top+1,log=log,headers=headers)
    columns=[i for i,h in enumerate(headers) if _canonical_header(h) in URL_FIELDS]
    if rows:
        links=read_links(ss,ws.title,top+1,top+len(rows),columns,rows,0,MetricReader(ss,log=log))
        for (offset,column),url in links.items():
            while len(rows[offset])<=column: rows[offset].append("")
            rows[offset][column]=url
    return [headers[i] for i in positions], [[_cell(row,i) for i in positions] for row in rows]


def publish_configured_sheet(cfg, gc, log=print):
    """Read and publish a configured sheet, without running aggregation or writing Sheets."""
    from fetch_posts import open_by_url_or_id, read_sheet_values
    from urllib.parse import urlsplit, parse_qs
    direct = getattr(cfg,"cf_publish_mode","merged") == "direct"
    if direct:
        urls, sheet, top = filter_source_settings(cfg)
        if isinstance(top, str):
            try: top = int(top)
            except ValueError as exc: raise ValueError('表头行必须是正整数') from exc
        if int(top) < 1: raise ValueError('表头行必须是正整数')
        if not urls: raise ValueError('请填写数据源表格链接')
        headers, rows = [], []
        for url in urls:
            ss = open_by_url_or_id(gc, url, log=log)
            names, values = read_post_source(ss, url, sheet, top, log,
                                            mappings=getattr(cfg, 'filter_header_mappings', []))
            if "帖文链接" not in _header_index(names, values):
                raise ValueError("缺少必需表头「帖文链接」，请检查表头行和字段映射")
            if set(map(_norm_header,names)) != set(map(_norm_header, names)):
                raise ValueError('数据源表头不一致，请检查字段映射')
            if not headers: headers = names
            index = {_norm_header(h): i for i, h in enumerate(names)}
            if set(index) != {_norm_header(h) for h in headers}:
                raise ValueError('数据源表头不一致，请检查字段映射')
            rows.extend([[_cell(row, index[_norm_header(h)]) for h in headers] for row in values])
        log(f'直接读取 {len(urls)} 个数据源，共 {len(rows)} 行；不筛选、不写入汇总表')
        return publish_assets_to_cloudflare(cfg, headers, rows, log=log, start_row=int(top)+1)
    elif str(cfg.cf_publish_source or "all").lower() == "hot":
        url, sheet = cfg.hot_target_url or cfg.target_url, cfg.hot_output_sheet or "高亮结果"
        start, include_headers = int(cfg.hot_start_row or 1), bool(cfg.hot_include_headers)
    else:
        url, sheet = cfg.target_url, cfg.output_sheet
        start, include_headers = int(cfg.output_start_row or 1), bool(cfg.include_headers)
    if not url: raise ValueError("请填写直接发布的表格链接" if direct else "请填写要发布的结果表链接")
    ss = open_by_url_or_id(gc,url,log=log)
    if sheet: ws = ss.worksheet(sheet)
    else:
        parsed=urlsplit(url);gid=(parse_qs(parsed.fragment).get('gid') or parse_qs(parsed.query).get('gid') or [None])[0]
        if gid is None: raise ValueError("请填写子表名称，或使用带 gid 的子表链接")
        ws=ss.get_worksheet_by_id(int(gid))
        if ws is None: raise ValueError("没有找到指定子表")
    values=read_sheet_values(ws,log=log)
    if direct and (len(values)<start or not _looks_like_header(values[start-1])):
        raise ValueError("指定行不是可识别的表头，请检查表头行")
    headers,rows,how,data_start=split_sheet_for_publish(values,start,include_headers)
    # Validate before any publishing request.
    index=_header_index(headers,rows)
    if "帖文链接" not in index: raise ValueError("缺少必需表头「帖文链接」")
    overlay_formula_urls(ws,rows,data_start,log=log,headers=headers)
    if rows:
        from sheet_links import read_links
        from metric_sheet_io import MetricReader
        columns = [index[name] for name in URL_FIELDS if name in index]
        links = read_links(ss,ws.title,data_start,data_start+len(rows)-1,columns,rows,0,MetricReader(ss,log=log))
        for (offset,column),url in links.items(): rows[offset][column]=url
    log(f"读取「{ws.title}」{len(rows)} 行（{how}），无需汇总即可发布")
    return publish_assets_to_cloudflare(cfg,headers,rows,log=log,start_row=data_start)


def _http_json(url: str, secret: str, payload: dict, log: LogFn) -> dict:
    from secure_http import credential_urlopen, validate_https
    validate_https(url)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _do():
        req = Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "x-publish-secret": secret,
                "Authorization": f"Bearer {secret}",
                "User-Agent": "Mozilla/5.0 sheets-post-filter",
            },
        )
        try:
            with credential_urlopen(req, timeout=180) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            raise RuntimeError(f"Cloudflare HTTP {e.code}：发布请求失败，请检查站点地址与服务状态") from e
        except URLError as e:
            raise RuntimeError(f"Cloudflare 连接失败: {e.reason}") from e

    return with_retry(_do, log=log, what="发布 Cloudflare")


def assets_fingerprint(assets: list[dict[str, Any]]) -> str:
    # Links and other display fields must trigger updates too.
    return hashlib.sha256(json.dumps(assets,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]


def publish_assets_to_cloudflare(
    cfg,
    headers: list[str],
    rows: list[list[Any]],
    group_values: list[Any] | None = None,
    log: LogFn = print,
    start_row: int = 1,
    skip_if_unchanged: bool = True,
) -> dict[str, Any]:
    from cf_accounts import validate_legacy_publish
    validate_legacy_publish(cfg)
    url = (getattr(cfg, "cf_publish_url", "") or "").strip()
    secret = (getattr(cfg, "cf_publish_secret", "") or "").strip()
    if not url or not secret:
        raise RuntimeError("请先填写 Cloudflare 发布地址和 CACHE_PUBLISH_SECRET")

    assets = rows_to_assets(
        headers,
        rows,
        group_values,
        log=log,
        start_row=max(1, int(start_row or 1)),
    )
    if not assets:
        sample = " | ".join(str(x) for x in (headers or [])[:12] if str(x).strip())
        hint = sample or "（起始行不是表头，已按列位置读取仍为空）"
        raise RuntimeError(f"没有可发布的记录（名字/帖文id/链接都为空）。表头样例：{hint}")

    fingerprint = assets_fingerprint(assets)
    destination_key = hashlib.sha256((url + '|' + str(getattr(cfg,'cf_profile_id','') or '')).encode()).hexdigest()[:24]
    prev = str(((load_sync_state() or {}).get('cf_publish_fingerprints') or {}).get(destination_key) or '')
    if skip_if_unchanged and prev and prev == fingerprint:
        log(f"数据和上次发布相同（{len(assets)} 条），跳过 Cloudflare，不占配额")
        return {
            "ok": True,
            "skipped": True,
            "totalRows": len(assets),
            "chunkCount": 0,
            "publicManifestUrl": "",
            "maxDate": "",
        }

    chunk_size = max(200, int(getattr(cfg, "cf_chunk_size", 800) or 800))
    build_id = str(uuid.uuid4())
    chunks = [assets[i : i + chunk_size] for i in range(0, len(assets), chunk_size)]
    max_date = ""
    for a in assets:
        d = str(a.get("date") or "")
        if d > max_date:
            max_date = d

    log(f"准备发布 Cloudflare：{len(assets)} 条，{len(chunks)} 个分片")
    for i, chunk in enumerate(chunks, 1):
        log(f"  正在上传分片 {i}/{len(chunks)}（{len(chunk)} 条）…")
        result = _http_json(
            url,
            secret,
            {
                "action": "put-chunk",
                "buildId": build_id,
                "index": i,
                "totalRows": len(assets),
                "maxDate": max_date,
                "assets": chunk,
            },
            log,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or f"分片 {i} 发布失败")
        log(f"  已上传分片 {i}/{len(chunks)}")

    log("正在 finalize…")
    final = _http_json(
        url,
        secret,
        {
            "action": "finalize",
            "buildId": build_id,
            "totalRows": len(assets),
            "chunkCount": len(chunks),
            "maxDate": max_date,
        },
        log,
    )
    if not final.get("ok"):
        raise RuntimeError(final.get("error") or "finalize 失败")
    log(f"Cloudflare 发布完成：{final.get('publicManifestUrl') or url}")
    remember_cf_fingerprint(fingerprint, len(assets))
    state = load_sync_state()
    state.setdefault('cf_publish_fingerprints', {})[destination_key] = fingerprint
    save_sync_state(state)
    return {
        "ok": True,
        "buildId": build_id,
        "totalRows": len(assets),
        "chunkCount": len(chunks),
        "publicManifestUrl": final.get("publicManifestUrl") or "",
        "maxDate": max_date,
    }
