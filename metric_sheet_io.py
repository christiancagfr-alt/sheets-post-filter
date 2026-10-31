"""Bounded, cancellable reads for the metric sync job.

FORMULA reads return literal cells without forcing Sheets to calculate an
entire workbook. Formula results are always requested from Google, never
evaluated locally or substituted with stale/blank values.
"""
from __future__ import annotations

import time
import re
from dataclasses import dataclass
from urllib.parse import quote

from fetch_posts import current_credential_pool, is_quota_error, is_transient_error, is_service_unavailable_error


class MetricReadUnavailable(RuntimeError):
    pass


@dataclass
class MetricSnapshot:
    values: list
    display: list
    formulas: list


def decode_snapshot(payload, sheet_id):
    """Keep numerical date serials, displayed metric text and actual formulas separate."""
    values, display, formulas = [], [], []
    found = False
    for sheet in payload.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") != sheet_id:
            continue
        found = True
        for grid in sheet.get("data", []):
            top, left = grid.get("startRow", 0), grid.get("startColumn", 0)
            for index, row in enumerate(grid.get("rowData", [])):
                target_row = top + index
                cells = row.get("values", [])
                for matrix in (values, display, formulas):
                    while len(matrix) <= target_row:
                        matrix.append([])
                    if len(matrix[target_row]) < left + len(cells):
                        matrix[target_row].extend([""] * (left + len(cells) - len(matrix[target_row])))
                for offset, cell in enumerate(cells):
                    col = left + offset
                    effective = cell.get("effectiveValue", {})
                    value = ""
                    for kind in ("numberValue", "stringValue", "boolValue"):
                        if kind in effective:
                            value = effective[kind]
                            break
                    if "errorValue" in effective:
                        value = cell.get("formattedValue", "#ERROR!")
                    # A numeric zero can have no displayed text (for example
                    # with a custom zero-hiding format). Do not invent "0"
                    # when Google deliberately returns no formattedValue.
                    text = cell.get("formattedValue", "")
                    values[target_row][col] = value
                    display[target_row][col] = str(text)
                    formulas[target_row][col] = cell.get("userEnteredValue", {}).get("formulaValue", "")
    if not found:
        raise MetricReadUnavailable("Google 返回的工作表与请求不一致，未使用该结果")
    return MetricSnapshot(values, display, formulas)


@dataclass
class MetricWorksheet:
    title: str
    id: int
    row_count: int
    col_count: int
    _properties: dict


class MetricSpreadsheet:
    """One metadata fetch per spreadsheet, shared by all worksheet lookups."""
    def __init__(self, identity, client):
        self.id = identity
        self.client = client
        self._worksheets = []

    def worksheets(self):
        return list(self._worksheets)

    def worksheet(self, title):
        from gspread.exceptions import WorksheetNotFound

        for sheet in self._worksheets:
            if sheet.title == title:
                return sheet
        raise WorksheetNotFound(title)


def open_metric_spreadsheet(gc, url, *, log, cancelled):
    from fetch_posts import extract_spreadsheet_id

    identity = extract_spreadsheet_id(url)
    if not identity:
        raise ValueError("无法从配置链接解析表格 ID")
    spreadsheet = MetricSpreadsheet(identity, gc.http_client)
    reader = MetricReader(spreadsheet, log=log, cancelled=cancelled)
    spreadsheet._metric_reader = reader

    def request():
        response = gc.http_client.session.get(
            "https://sheets.googleapis.com/v4/spreadsheets/" + identity,
            params={"fields": "sheets(properties(sheetId,title,hidden,gridProperties(rowCount,columnCount)))", "prettyPrint": "false"},
            timeout=(8, reader.timeout),
        )
        if not response.ok:
            from gspread.exceptions import APIError

            raise APIError(response)
        return response.json()

    metadata = reader._read_routes([request, request, request], what="读取工作表目录", calculated=False)
    for entry in metadata.get("sheets", []):
        properties = entry["properties"]
        grid = properties.get("gridProperties", {})
        worksheet = MetricWorksheet(
            title=properties["title"], id=properties["sheetId"],
            row_count=int(grid.get("rowCount", 1)),
            col_count=int(grid.get("columnCount", 1)), _properties=properties,
        )
        worksheet._metric_reader = reader
        spreadsheet._worksheets.append(worksheet)
    return spreadsheet


class MetricReader:
    def __init__(self, spreadsheet, *, log, cancelled=lambda: False, timeout=15):
        self.spreadsheet = spreadsheet
        self.log = log
        self.cancelled = cancelled
        self.timeout = timeout
        self.calls = 0
        self.seconds = 0.0

    def check_cancelled(self):
        if self.cancelled():
            raise RuntimeError("任务已取消")

    def _request(self, a1_range, render):
        client = self.spreadsheet.client
        session = getattr(client, "session", None)
        if session is None:
            return self.spreadsheet.values_get(
                a1_range, params={"majorDimension": "ROWS", "valueRenderOption": render}
            )
        url = (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            + self.spreadsheet.id
            + "/values/"
            + quote(a1_range, safe="")
        )
        response = session.get(
            url,
            params={"majorDimension": "ROWS", "valueRenderOption": render, "prettyPrint": "false"},
            timeout=(8, self.timeout),
        )
        if not response.ok:
            from gspread.exceptions import APIError

            raise APIError(response)
        return response.json()

    def execute(self, operation, *, what, calculated=False, attempts=3):
        self.check_cancelled()
        started = time.perf_counter()
        pool = current_credential_pool()
        try:
            for attempt in range(attempts):
                self.check_cancelled()
                self.calls += 1
                try:
                    result = operation()
                    self.log(f"{what}完成：{time.perf_counter() - started:.1f} 秒")
                    return result
                except Exception as exc:
                    text = str(exc).lower()
                    quota = is_quota_error(exc)
                    service = any(marker in text for marker in ("502", "503", "504", "backend error"))
                    # A timed-out calculation cannot be fixed by cycling 14
                    # credentials. Return a precise failure at the first timeout.
                    timeout = "timeout" in type(exc).__name__.lower() or "timed out" in text
                    if timeout:
                        detail = (
                            "Google 暂时无法返回此范围的现有数值/显示文本。即使只复制文字，也需要 Google 先返回这些内容。"
                            if calculated
                            else "Google 请求未及时完成。"
                        )
                        raise MetricReadUnavailable(
                            f"{what}超时（{self.timeout} 秒）。{detail}"
                        ) from exc
                    if not is_transient_error(exc) or attempt == attempts - 1:
                        raise
                    if (quota or service) and pool is not None and pool.rotate(f"{what}接口异常"):
                        self.log(f"{what}切换服务账号后重试（{attempt + 1}/3）")
                        continue
                    for _ in range((attempt + 1) * 5):
                        self.check_cancelled()
                        time.sleep(0.2)
        finally:
            self.seconds += time.perf_counter() - started

    def _response_json(self, response):
        if not response.ok:
            from gspread.exceptions import APIError

            raise APIError(response)
        return response.json()

    def _grid_range(self, a1_range):
        match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)", a1_range)
        if match is None:
            raise ValueError("指标读取范围必须包含确定的工作表和起止行列")
        from fetch_posts import col_letter_to_index

        title, left, top, right, bottom = match.groups()
        worksheet = self.spreadsheet.worksheet(title.replace("''", "'"))
        return {
            "sheetId": worksheet.id,
            "startRowIndex": int(top) - 1, "endRowIndex": int(bottom),
            "startColumnIndex": col_letter_to_index(left),
            "endColumnIndex": col_letter_to_index(right) + 1,
        }

    def _read_routes(self, routes, *, what, calculated=True):
        for index, operation in enumerate(routes):
            self.check_cancelled()
            try:
                return self.execute(operation, what=what, calculated=calculated, attempts=1)
            except Exception as exc:
                retryable = isinstance(exc, MetricReadUnavailable) or is_transient_error(exc)
                if not retryable or index == len(routes) - 1:
                    raise
                pool = current_credential_pool()
                if (is_quota_error(exc) or is_service_unavailable_error(exc)) and pool is not None:
                    pool.rotate(f"{what}接口暂不可用")
                self.log(f"{what}暂未返回，切换备用读取（{index + 2}/{len(routes)}）")
                for _ in range(5):
                    self.check_cancelled()
                    time.sleep(0.2)

    def _filtered_values(self, a1_range, render):
        response = self.spreadsheet.client.session.post(
            "https://sheets.googleapis.com/v4/spreadsheets/" + self.spreadsheet.id + "/values:batchGetByDataFilter",
            params={"fields": "valueRanges(valueRange(values))", "prettyPrint": "false"},
            json={"dataFilters": [{"gridRange": self._grid_range(a1_range)}], "valueRenderOption": render,
                  "dateTimeRenderOption": "SERIAL_NUMBER", "majorDimension": "ROWS"},
            timeout=(8, self.timeout),
        )
        payload = self._response_json(response)
        matches = payload.get("valueRanges", [])
        if len(matches) != 1:
            raise MetricReadUnavailable("备用读取返回了不匹配的范围数量")
        return matches[0].get("valueRange", {})

    def read(self, a1_range, *, render="FORMULA", what="读取数据"):
        primary = lambda: self._request(a1_range, render)
        routes = [primary]
        if getattr(self.spreadsheet.client, "session", None) is not None:
            routes += [lambda: self._filtered_values(a1_range, render), primary]
        response = self._read_routes(routes, what=what, calculated=render != "FORMULA")
        return (response or {}).get("values") or []

    def snapshot(self, worksheet, *, include_formulas=True):
        """One requested sheet, no styles; fetch display text and date values together."""
        from fetch_posts import index_to_col_letter

        a1_range = (
            "'" + worksheet.title.replace("'", "''") + "'!A1:"
            + index_to_col_letter(max(0, worksheet.col_count - 1)) + str(max(1, worksheet.row_count))
        )
        url = "https://sheets.googleapis.com/v4/spreadsheets/" + self.spreadsheet.id
        cell_fields = "effectiveValue,formattedValue"
        if include_formulas:
            cell_fields += ",userEnteredValue(formulaValue)"
        fields = "sheets(properties(sheetId),data(startRow,startColumn,rowData(values(" + cell_fields + "))))"

        def primary():
            return self._response_json(self.spreadsheet.client.session.get(
                url, params={"ranges": a1_range, "fields": fields, "prettyPrint": "false"}, timeout=(8, self.timeout),
            ))

        def alternate():
            return self._response_json(self.spreadsheet.client.session.post(
                url + ":getByDataFilter", params={"fields": fields, "prettyPrint": "false"},
                json={"dataFilters": [{"gridRange": self._grid_range(a1_range)}], "includeGridData": True},
                timeout=(8, self.timeout),
            ))

        payload = self._read_routes([primary, alternate, primary], what=f"读取「{worksheet.title}」数值和显示文本")
        return decode_snapshot(payload, worksheet.id)

    def write(self, batch, *, input_option="USER_ENTERED", what="写入指标"):
        client = self.spreadsheet.client

        def request():
            if getattr(client, "session", None) is None:
                return self.spreadsheet.values_batch_update({"data": batch, "valueInputOption": input_option})
            response = client.session.post(
                "https://sheets.googleapis.com/v4/spreadsheets/" + self.spreadsheet.id + "/values:batchUpdate",
                json={"data": batch, "valueInputOption": input_option},
                timeout=(8, self.timeout),
            )
            if not response.ok:
                from gspread.exceptions import APIError

                raise APIError(response)
            return response.json()

        return self.execute(request, what=what)


def is_formula(value):
    return isinstance(value, str) and value.startswith("=")


def resolve_formula_cells(spreadsheet, title, rows, coordinates, *, read, log):
    """Resolve only required formula cells, grouping adjacent cells per row."""
    from fetch_posts import index_to_col_letter

    pending = sorted({(r, c) for r, c in coordinates if r < len(rows) and c < len(rows[r]) and is_formula(rows[r][c])})
    if not pending:
        return
    log(f"「{title}」读取 {len(pending)} 个匹配字段的现有值")
    quoted = "'" + title.replace("'", "''") + "'"
    # Consecutive formulas down the date column are one request, not one
    # request per day. Identical vertical spans in adjacent columns merge too.
    vertical = sorted(pending, key=lambda cell: (cell[1], cell[0]))
    spans = []
    index = 0
    while index < len(vertical):
        first_row, col = vertical[index]
        last_row = first_row
        index += 1
        while index < len(vertical) and vertical[index] == (last_row + 1, col):
            last_row += 1
            index += 1
        spans.append((first_row, last_row, col, col))
    spans.sort()
    rectangles = []
    for first_row, last_row, col, _ in spans:
        if rectangles and rectangles[-1][:2] == (first_row, last_row) and rectangles[-1][3] + 1 == col:
            before = rectangles[-1]
            rectangles[-1] = (first_row, last_row, before[2], col)
        else:
            rectangles.append((first_row, last_row, col, col))
    first_row = min(row for row, _col in pending)
    last_row = max(row for row, _col in pending)
    first_col = min(col for _row, col in pending)
    last_col = max(col for _row, col in pending)
    area = (last_row - first_row + 1) * (last_col - first_col + 1)
    if len(rectangles) > 2 and area <= 20_000:
        rectangles = [(first_row, last_row, first_col, last_col)]
    pending_set = set(pending)
    for first_row, last_row, first_col, last_col in rectangles:
        a1 = f"{quoted}!{index_to_col_letter(first_col)}{first_row + 1}:{index_to_col_letter(last_col)}{last_row + 1}"
        block = read(a1, render="UNFORMATTED_VALUE", what=f"读取「{title}」匹配字段现有值")
        for row in range(first_row, last_row + 1):
            values = block[row - first_row] if row - first_row < len(block) else []
            for col in range(first_col, last_col + 1):
                if (row, col) not in pending_set:
                    continue
                value = values[col - first_col] if col - first_col < len(values) else ""
                if is_formula(value):
                    raise RuntimeError(f"「{title}」公式结果未解析：{index_to_col_letter(col)}{row + 1}")
                rows[row][col] = value
