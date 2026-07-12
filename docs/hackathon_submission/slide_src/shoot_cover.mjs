import { chromium } from "@playwright/test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
// slide_src/ -> hackathon_submission/slide_png/
const outPath = join(here, "..", "slide_png", "slide1_cover_new.png");

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 880, height: 495 },
  deviceScaleFactor: 2,
});

const url = pathToFileURL(join(here, "slide1.html")).href;
await page.goto(url, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
const el = await page.$(".slide");
await el.screenshot({ path: outPath });
console.log("wrote", outPath);

await browser.close();
