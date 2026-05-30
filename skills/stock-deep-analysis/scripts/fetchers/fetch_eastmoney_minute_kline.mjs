#!/usr/bin/env node

import fs from "node:fs";
import https from "node:https";
import path from "node:path";
import { execFileSync } from "node:child_process";

const HOME = process.env.HOME || "";
const DEFAULT_TUSHARE_ROOT = path.join(HOME, "quant-data", "tushare");
const TUSHARE_ROOT = process.env.TUSHARE_ROOT || DEFAULT_TUSHARE_ROOT;
const STOCK_DATA_ROOT = process.env.STOCK_DATA_ROOT || path.join(TUSHARE_ROOT, "股票数据");
const DATA_ROOT = `${STOCK_DATA_ROOT}/分钟数据`;

const symbol = process.argv[2] || "002639";
const secid = symbol.startsWith("6") ? `1.${symbol}` : `0.${symbol}`;
const quotePageUrl = `https://quote.eastmoney.com/q/${secid}.html`;
const requestedOutFile = process.argv[3] || "";

const cleanEnv = {
  ...process.env,
  ALL_PROXY: "",
  all_proxy: "",
  HTTPS_PROXY: "",
  https_proxy: "",
  HTTP_PROXY: "",
  http_proxy: "",
};

const apiUrl = new URL("https://push2his.eastmoney.com/api/qt/stock/trends2/get");
apiUrl.searchParams.set("secid", secid);
apiUrl.searchParams.set("fields1", "f1,f2,f3,f4,f5,f6,f7,f8");
apiUrl.searchParams.set("fields2", "f51,f52,f53,f54,f55,f56,f57,f58");
apiUrl.searchParams.set("ndays", "1");
apiUrl.searchParams.set("iscr", "0");
apiUrl.searchParams.set("iscca", "0");
apiUrl.searchParams.set("ut", "fa5fd1943c7b386f172d6893dbfba10b");

const sseUrl = new URL("https://push2.eastmoney.com/api/qt/stock/trends2/sse");
sseUrl.searchParams.set(
  "fields1",
  "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f17"
);
sseUrl.searchParams.set("fields2", "f51,f52,f53,f54,f55,f56,f57,f58");
sseUrl.searchParams.set("mpi", "1000");
sseUrl.searchParams.set("ut", "fa5fd1943c7b386f172d6893dbfba10b");
sseUrl.searchParams.set("secid", secid);
sseUrl.searchParams.set("ndays", "1");
sseUrl.searchParams.set("iscr", "0");
sseUrl.searchParams.set("iscca", "0");
sseUrl.searchParams.set("wbp2u", "|0|0|0|web");

function fetchJsonByHttps(inputUrl) {
  return new Promise((resolve, reject) => {
    const req = https.get(
      inputUrl,
      {
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
          Accept: "application/json,text/plain,*/*",
          Referer: "https://quote.eastmoney.com/",
        },
      },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          body += chunk;
        });
        res.on("end", () => {
          if (res.statusCode !== 200) {
            reject(
              new Error(`HTTP ${res.statusCode || "unknown"}: ${body.slice(0, 200)}`)
            );
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (err) {
            reject(new Error(`JSON parse failed: ${err.message}`));
          }
        });
      }
    );

    req.on("error", reject);
  });
}

function fetchJsonByCurl(inputUrl) {
  const out = execFileSync(
    "curl",
    [
      "-sS",
      "--retry",
      "3",
      "--retry-delay",
      "1",
      "-H",
      "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "-H",
      "Accept: application/json,text/plain,*/*",
      "-H",
      "Referer: https://quote.eastmoney.com/",
      String(inputUrl),
    ],
    { encoding: "utf8", env: cleanEnv }
  );
  return JSON.parse(out);
}

function fetchJsonByPlaywrightSse(pageUrl, eventUrl) {
  const pythonScript = `
import json
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import Stealth
except Exception:
    Stealth = None

PAGE_URL = ${JSON.stringify(pageUrl)}
SSE_URL = ${JSON.stringify(String(eventUrl))}

JS = """
async ({ sseUrl }) => {
  return await new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), 10000);
    const es = new EventSource(sseUrl, { withCredentials: true });
    es.onerror = () => {
      clearTimeout(timer);
      try { es.close(); } catch (e) {}
      resolve(null);
    };
    es.onmessage = (e) => {
      clearTimeout(timer);
      try { es.close(); } catch (err) {}
      resolve(e.data);
    };
  });
}
"""

def capture(playwright):
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page()
    page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    payload = page.evaluate(JS, {"sseUrl": SSE_URL})
    browser.close()
    return payload

if Stealth is not None:
    with Stealth().use_sync(sync_playwright()) as p:
        payload = capture(p)
else:
    with sync_playwright() as p:
        payload = capture(p)

if not payload:
    raise SystemExit("FAILED_TO_FETCH_SSE")

print(payload)
`;

  const out = execFileSync("python3", ["-c", pythonScript], {
    encoding: "utf8",
    maxBuffer: 10 * 1024 * 1024,
    env: cleanEnv,
  });
  return JSON.parse(out);
}

function toNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : NaN;
}

function resolveOutputPath(rows) {
  if (requestedOutFile) {
    return path.resolve(process.cwd(), requestedOutFile);
  }
  const tradeDate = rows[0].datetime.slice(0, 10);
  const [y, m, d] = tradeDate.split("-");
  const dir = path.join(DATA_ROOT, y, m, d, secid.startsWith("1.") ? `${symbol}.SH` : `${symbol}.SZ`);
  return path.join(dir, "1m.csv");
}

let data;
try {
  data = await fetchJsonByHttps(apiUrl);
} catch (httpsError) {
  try {
    data = fetchJsonByCurl(apiUrl);
  } catch (curlError) {
    try {
      data = fetchJsonByPlaywrightSse(quotePageUrl, sseUrl);
    } catch (playwrightError) {
      throw new Error(
        [
          "No minute trend data returned from Eastmoney.",
          `https fallback: ${httpsError.message}`,
          `curl fallback: ${curlError.message}`,
          `playwright SSE fallback: ${playwrightError.message}`,
        ].join("\n")
      );
    }
  }
}

if (!data || !data.data || !Array.isArray(data.data.trends)) {
  throw new Error("No minute trend data returned from Eastmoney.");
}

const rows = data.data.trends.map((line) => {
  const [datetime, open, close, high, low, volume, amount, avg] = line.split(",");
  return {
    datetime,
    open: toNumber(open),
    close: toNumber(close),
    high: toNumber(high),
    low: toNumber(low),
    volume: toNumber(volume),
    amount: toNumber(amount),
    avg: toNumber(avg),
  };
});

const outputPath = resolveOutputPath(rows);
fs.mkdirSync(path.dirname(outputPath), { recursive: true });

const csvHeader = "datetime,open,close,high,low,volume,amount,avg\n";
const csvBody = rows
  .map(
    (r) =>
      `${r.datetime},${r.open},${r.close},${r.high},${r.low},${r.volume},${r.amount},${r.avg}`
  )
  .join("\n");
fs.writeFileSync(outputPath, csvHeader + csvBody + "\n", "utf8");

const first = rows[0];
const last = rows[rows.length - 1];
const dayHigh = Math.max(...rows.map((r) => r.high));
const dayLow = Math.min(...rows.map((r) => r.low));
const prevClose = toNumber(data.data.preClose);
const change = Number.isFinite(prevClose) ? last.close - prevClose : NaN;
const pct = Number.isFinite(prevClose) && prevClose !== 0 ? (change / prevClose) * 100 : NaN;

console.log(`name: ${data.data.name} (${symbol})`);
console.log(`date: ${first.datetime.slice(0, 10)}`);
console.log(`minutes: ${rows.length}`);
console.log(
  `open: ${first.open.toFixed(2)}  close: ${last.close.toFixed(2)}  high: ${dayHigh.toFixed(2)}  low: ${dayLow.toFixed(2)}`
);
if (Number.isFinite(change) && Number.isFinite(pct)) {
  console.log(`vs prev close(${prevClose.toFixed(2)}): ${change.toFixed(2)} (${pct.toFixed(2)}%)`);
}
console.log(`csv: ${outputPath}`);
