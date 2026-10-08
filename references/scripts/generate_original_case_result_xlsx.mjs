import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

/**
 * 将父用例聚合结果追加到原始 Excel 的副本中。
 * 原始文件不会被覆盖；输出工作簿保留原表格并新增结果列。
 */
const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, values) => {
  if (!value.startsWith("--")) return pairs;
  pairs.push([value.slice(2), values[index + 1]]);
  return pairs;
}, []));

const required = ["source-xlsx", "aggregation-json", "output-xlsx"];
for (const key of required) {
  if (!args[key]) throw new Error(`缺少参数 --${key}`);
}

const STATUS_LABELS = {
  passed: "通过",
  failed: "未通过",
  blocked: "暂无法判断",
  not_run: "未执行",
};

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function cleanAssertion(value) {
  return cleanText(value).replace(/^\d+[.、]\s*/, "");
}

function unique(items) {
  return [...new Set(items.map(cleanText).filter(Boolean))];
}

function compactList(items, limit = 3) {
  const values = unique(items);
  if (!values.length) return "";
  const shown = values.slice(0, limit).join("；");
  return values.length > limit ? `${shown}；另有 ${values.length - limit} 项` : shown;
}

function humanBlockerReason(reason) {
  const value = cleanText(reason);
  const mappings = [
    ["真实支付资金流", "需要执行真实支付，当前为保护测试环境未自动操作。"],
    ["H5/小程序跨端会话", "需要在 H5 或小程序端配合操作，当前只执行了运营后台。"],
    ["短信通道", "需要短信通道配合，当前环境无法核验短信内容。"],
    ["跨端业务数据回流", "需要用户端产生业务数据，再回到后台核对统计结果。"],
    ["订单资金操作", "需要退款或支付等订单资金操作，当前批次未自动执行。"],
    ["订单履约状态操作", "需要发货、收货或核销等履约操作，当前批次未自动执行。"],
    ["多账号权限矩阵", "需要不同角色账号验证权限，当前只有管理员账号。"],
    ["连点时序控制", "需要重复点击和幂等性观察能力，当前执行器尚未覆盖。"],
    ["真实时间流转", "需要等待活动开始或结束，当前批次没有可控时间条件。"],
    ["并发压测通道", "需要并发测试工具，当前浏览器执行无法完成。"],
    ["订单详情已打开但未命中预期展示锚点", "缺少符合指定类型或状态的订单，当前订单无法用于验证。"],
    ["数据入口点击后未发生页面跳转", "执行工具未能正确进入效果数据页面。"],
    ["前置业务数据缺失", "测试环境缺少符合用例前置条件的数据。"],
    ["前置缺失", "测试环境缺少符合用例前置条件的数据。"],
    ["断言口径无法映射为页面锚点", "页面上没有可稳定定位、可直接核对该预期的内容。"],
    ["表单或页面结构填写受阻", "执行工具未能正确操作页面表单控件。"],
    ["观察值结构不完整", "页面操作已执行，但没有取得足够证据判断结果。"],
    ["未观察到拒绝证据", "未能确认系统是否正确拒绝了无效操作。"],
    ["负向校验未取得完整结构化观察", "未能确认无效操作是否被阻止，以及数据是否未写入。"],
  ];
  const matched = mappings.find(([keyword]) => value.includes(keyword));
  return matched ? matched[1] : value || "当前条件不足，暂时无法形成可靠结论。";
}

function progressText(result) {
  const total = Number(result.child_count || 0);
  const passed = Number(result.passed_count || 0);
  const failed = Number(result.failed_count || 0);
  const blocked = Number(result.blocked_count || 0);
  const notRun = Number(result.not_run_count || 0);
  if (result.status === "passed") return `${total} 项检查全部通过`;
  const parts = [`共 ${total} 项`];
  if (passed) parts.push(`${passed} 项通过`);
  if (failed) parts.push(`${failed} 项未通过`);
  if (blocked) parts.push(`${blocked} 项暂无法判断`);
  if (notRun) parts.push(`${notRun} 项未执行`);
  return parts.join("，");
}

function humanExplanation(result) {
  if (result.status === "passed") return "所有拆分检查均达到预期。";
  if (result.status === "failed") {
    const assertions = (result.failed_child_cases || []).map((item) =>
      cleanAssertion(item.assertion || item.title)
    );
    return `未达到预期：${compactList(assertions) || "存在检查项未通过"}。`;
  }
  if (result.status === "blocked") {
    const reasons = (result.blocked_child_cases || []).map((item) =>
      humanBlockerReason(item.blocker_reason || item.failure_reason)
    );
    return compactList(reasons) || "当前条件不足，暂时无法形成可靠结论。";
  }
  return "尚未执行，当前没有可用结果。";
}

function nextAction(result) {
  if (result.status === "passed") return "无需处理。";
  const raw = cleanText([
    result.failure_reason,
    result.blocker_reason,
  ].filter(Boolean).join(" "));
  if (result.status === "failed") {
    if (raw.includes("搜索[") && raw.includes("列表 0 行")) {
      return "先确认测试环境存在目标数据，再复测搜索结果；仍不符合时按缺陷处理。";
    }
    if (raw.includes("缺失锚点")) {
      return "结合截图人工复核页面行为；确认不符合预期后提交缺陷。";
    }
    return "查看截图和技术明细并人工复核；确认不符合预期后提交缺陷。";
  }
  const actions = [];
  if (raw.includes("H5/小程序")) actions.push("接入 H5 或小程序测试端后重跑");
  if (raw.includes("真实支付") || raw.includes("订单资金")) actions.push("准备可控支付沙箱或测试账号后重跑");
  if (raw.includes("多账号权限")) actions.push("补充不同角色账号和权限矩阵后重跑");
  if (raw.includes("前置") || raw.includes("订单详情")) actions.push("准备符合条件的活动或订单数据后重跑");
  if (raw.includes("工具定位") || raw.includes("结构不完整") || raw.includes("页面锚点") || raw.includes("页面跳转")) {
    actions.push("修复页面定位或结果观察器后重跑");
  }
  if (raw.includes("时间流转")) actions.push("提供可控时间条件后重跑");
  return `${compactList(actions, 2) || "补齐前置数据或执行能力后重跑"}。`;
}

function auditDetails(result) {
  const failed = (result.failed_child_cases || []).map((item) =>
    `${item.case_id} | ${cleanAssertion(item.assertion || item.title)} | ${cleanText(item.failure_reason || item.actual_result)}`
  );
  const blocked = (result.blocked_child_cases || []).map((item) =>
    `${item.case_id} | ${cleanText(item.blocker_type) || "阻塞"} | ${cleanText(item.blocker_reason || item.failure_reason)}`
  );
  return [...failed, ...blocked].join("\n");
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(args["source-xlsx"]));
const sheet = workbook.worksheets.getItemAt(0);
const used = sheet.getUsedRange();
const values = used.values || [];
if (!values.length) throw new Error("原始工作簿没有可编辑数据");

const aggregation = JSON.parse(await fs.readFile(args["aggregation-json"], "utf8"));
const byRow = new Map((aggregation.rows || []).map((row) => [Number(row.source_row), row]));
const headers = [
  "执行结论",
  "检查情况",
  "一句话说明",
  "建议下一步",
  "子用例ID（审计）",
  "技术明细（审计）",
  "判定规则（审计）",
];
const headerRow = values[0] || [];
const firstNewColumn = headerRow.length;
sheet.getRangeByIndexes(0, firstNewColumn, 1, headers.length).values = [headers];

const rows = [];
for (let rowIndex = 1; rowIndex < values.length; rowIndex += 1) {
  const sourceRow = rowIndex + 1;
  const result = byRow.get(sourceRow);
  rows.push(result ? [
    STATUS_LABELS[result.status] || result.status || "",
    progressText(result),
    humanExplanation(result),
    nextAction(result),
    (result.child_case_ids || []).join("\n"),
    auditDetails(result),
    result.status_rule || "",
  ] : headers.map(() => ""));
}
if (rows.length) {
  sheet.getRangeByIndexes(1, firstNewColumn, rows.length, headers.length).values = rows;
}

const resultRange = sheet.getRangeByIndexes(0, firstNewColumn, Math.max(1, rows.length + 1), headers.length);
resultRange.format.wrapText = true;
resultRange.format.verticalAlignment = "top";
sheet.getRangeByIndexes(0, firstNewColumn, 1, headers.length).format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
};
resultRange.format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
sheet.getRangeByIndexes(0, firstNewColumn, Math.max(1, rows.length + 1), 1).format.columnWidth = 14;
sheet.getRangeByIndexes(0, firstNewColumn + 1, Math.max(1, rows.length + 1), 1).format.columnWidth = 26;
sheet.getRangeByIndexes(0, firstNewColumn + 2, Math.max(1, rows.length + 1), 1).format.columnWidth = 54;
sheet.getRangeByIndexes(0, firstNewColumn + 3, Math.max(1, rows.length + 1), 1).format.columnWidth = 42;
sheet.getRangeByIndexes(0, firstNewColumn + 4, Math.max(1, rows.length + 1), 1).format.columnWidth = 24;
sheet.getRangeByIndexes(0, firstNewColumn + 5, Math.max(1, rows.length + 1), 1).format.columnWidth = 58;
sheet.getRangeByIndexes(0, firstNewColumn + 6, Math.max(1, rows.length + 1), 1).format.columnWidth = 36;
const statusRange = sheet.getRangeByIndexes(1, firstNewColumn, Math.max(1, rows.length), 1);
statusRange.conditionalFormats.addCustom(`=$${String.fromCharCode(65 + firstNewColumn)}2="通过"`, {
  fill: "#E2F0D9", font: { bold: true, color: "#375623" },
});
statusRange.conditionalFormats.addCustom(`=$${String.fromCharCode(65 + firstNewColumn)}2="未通过"`, {
  fill: "#FCE4D6", font: { bold: true, color: "#9C0006" },
});
statusRange.conditionalFormats.addCustom(`=$${String.fromCharCode(65 + firstNewColumn)}2="暂无法判断"`, {
  fill: "#FFF2CC", font: { bold: true, color: "#7F6000" },
});
sheet.freezePanes.freezeRows(1);

const readable = workbook.worksheets.add("结果阅读版");
const summary = aggregation.summary || {};
readable.getRange("A1").values = [["原始用例执行结果"]];
readable.getRange("A1").format.font = { bold: true, size: 16, color: "#1F1F1F" };
readable.getRange("A2").values = [["未通过表示已有证据未满足预期；暂无法判断表示缺少数据、通道或证据，不等于产品失败。"]];
readable.getRange("A2").format.font = { italic: true, color: "#595959" };
readable.getRange("A3").values = [["数据来源：规范化测试结果聚合。原始 Excel 内容保持不变。"]];
readable.getRange("A3").format.font = { color: "#7F7F7F" };
readable.getRange("A5:H5").values = [[
  "原始用例", summary.original_case_count || 0,
  "通过", summary.passed || 0,
  "未通过", summary.failed || 0,
  "暂无法判断", summary.blocked || 0,
]];
readable.getRange("A5:H5").format = {
  fill: "#D9EAF7",
  font: { bold: true, color: "#1F1F1F" },
  verticalAlignment: "center",
};
readable.getRange("A5:H5").format.borders = { preset: "all", style: "thin", color: "#B4C6E7" };

const readableHeaders = ["原始用例ID", "目录", "用例标题", "优先级", "执行结论", "检查情况", "一句话说明", "建议下一步", "原表行号"];
readable.getRange("A8:I8").values = [readableHeaders];
const readableRows = (aggregation.rows || []).map((result) => {
  const original = values[Number(result.source_row) - 1] || [];
  return [
    result.source_case_id || "",
    original[0] || "",
    result.source_title || original[1] || "",
    original[2] || "",
    STATUS_LABELS[result.status] || result.status || "",
    progressText(result),
    humanExplanation(result),
    nextAction(result),
    Number(result.source_row) || "",
  ];
});
if (readableRows.length) readable.getRangeByIndexes(8, 0, readableRows.length, readableHeaders.length).values = readableRows;
const readableTableRange = readable.getRangeByIndexes(7, 0, readableRows.length + 1, readableHeaders.length);
readableTableRange.format.wrapText = true;
readableTableRange.format.verticalAlignment = "top";
readable.getRange("A8:I8").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
readable.getRangeByIndexes(7, 0, readableRows.length + 1, readableHeaders.length).format.borders = {
  preset: "all", style: "thin", color: "#D9E2F3",
};
const widths = [16, 30, 44, 10, 14, 26, 54, 42, 10];
widths.forEach((width, index) => {
  readable.getRangeByIndexes(7, index, readableRows.length + 1, 1).format.columnWidth = width;
});
const readableStatus = readable.getRangeByIndexes(8, 4, Math.max(1, readableRows.length), 1);
readableStatus.conditionalFormats.addCustom('=$E9="通过"', {
  fill: "#E2F0D9", font: { bold: true, color: "#375623" },
});
readableStatus.conditionalFormats.addCustom('=$E9="未通过"', {
  fill: "#FCE4D6", font: { bold: true, color: "#9C0006" },
});
readableStatus.conditionalFormats.addCustom('=$E9="暂无法判断"', {
  fill: "#FFF2CC", font: { bold: true, color: "#7F6000" },
});
readable.freezePanes.freezeRows(8);
const table = readable.tables.add(`A8:I${readableRows.length + 8}`, true, "ReadableCaseResults");
table.style = "TableStyleMedium2";

const preview = await workbook.render({ sheetName: readable.name, range: "A1:I22", scale: 1, format: "png" });
await fs.writeFile(`${args["output-xlsx"]}.preview.png`, new Uint8Array(await preview.arrayBuffer()));
const inspection = await workbook.inspect({
  kind: "table",
  range: `${readable.name}!A1:I18`,
  include: "values,formulas",
  tableMaxRows: 18,
  tableMaxCols: 9,
});
await fs.writeFile(`${args["output-xlsx"]}.inspect.ndjson`, inspection.ndjson, "utf8");
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "最终公式错误检查",
});
if (errors.ndjson && errors.ndjson.includes('"matches":[') && !errors.ndjson.includes('"matches":[]')) {
  console.warn("检测到公式错误，请检查 inspect 输出。", errors.ndjson);
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(args["output-xlsx"]);
console.log(`Generated original-case result workbook: ${args["output-xlsx"]}`);
