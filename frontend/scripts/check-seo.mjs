import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const site = JSON.parse(readFileSync(join(root, "src/content/site.json"), "utf8"));
const html = readFileSync(join(root, "index.html"), "utf8");
const distHtml = readFileSync(join(root, "dist/index.html"), "utf8");

function ld(from) {
  const m = from.match(/<script type="application\/ld\+json">\s*([\s\S]*?)<\/script>/);
  if (!m) throw new Error("JSON-LD missing");
  return JSON.parse(m[1]);
}

const graph = ld(html);
const faqNode = graph["@graph"].find((n) => n["@type"] === "FAQPage");
const questions = faqNode.mainEntity.map((e) => e.name);
const answers = faqNode.mainEntity.map((e) => e.acceptedAnswer.text);
if (questions.join("|") !== site.faq.map((f) => f.q).join("|")) {
  throw new Error("FAQ JSON-LD questions drifted from site.json");
}
if (answers.join("|") !== site.faq.map((f) => f.a).join("|")) {
  throw new Error("FAQ JSON-LD answers drifted from site.json");
}
if (!html.includes(`<h1`) || !html.includes(site.headline)) {
  throw new Error("static fallback is missing the H1");
}
if (!graph["@graph"].some((n) => n["@type"] === "Person" && n.name === site.founder.name)) {
  throw new Error("Person JSON-LD missing founder");
}
if (!html.includes(site.inspecTher.url) || !html.includes(site.founder.url)) {
  throw new Error("static HTML is missing founder or Inspec-Ther links");
}
if (!distHtml.includes(site.founder.name) || !distHtml.includes("inspec-ther.com")) {
  throw new Error("built HTML lost founder or Inspec-Ther");
}
for (const name of ["robots.txt", "llms.txt", "sitemap.xml", ".well-known/security.txt", "404.html", "og.png"]) {
  const path = join(root, "dist", name);
  try {
    readFileSync(path);
  } catch {
    throw new Error(`dist is missing ${name}`);
  }
}
if (!distHtml.includes("application/ld+json")) throw new Error("built HTML lost JSON-LD");
if (distHtml.includes("/login") || distHtml.includes("Sign in")) throw new Error("login leaked into landing");
console.log("seo checks ok");
