import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const site = JSON.parse(readFileSync(join(root, "src/content/site.json"), "utf8"));
const siteUrl = `${site.siteUrl}/`;
const founderId = `${siteUrl}#founder`;
const appId = `${siteUrl}#app`;
const inspecId = `${siteUrl}#inspec-ther`;

const graph = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "WebSite",
      "@id": `${siteUrl}#website`,
      name: site.name,
      url: siteUrl,
      description: site.description,
      publisher: { "@id": founderId },
    },
    {
      "@type": "Person",
      "@id": founderId,
      name: site.founder.name,
      alternateName: site.founder.alternateName,
      jobTitle: site.founder.jobTitle,
      url: site.founder.url,
      sameAs: [site.founder.url],
      worksFor: { "@type": "Organization", name: site.founder.worksFor },
    },
    {
      "@type": "SoftwareApplication",
      "@id": appId,
      name: site.name,
      applicationCategory: "BusinessApplication",
      operatingSystem: "Linux, macOS, Windows",
      license: "https://www.apache.org/licenses/LICENSE-2.0",
      url: siteUrl,
      description: site.definition,
      featureList: site.features,
      author: { "@id": founderId },
      isRelatedTo: [{ "@id": inspecId }, { "@id": `${siteUrl}#athena-vision` }],
    },
    {
      "@type": "SoftwareApplication",
      "@id": `${siteUrl}#athena-vision`,
      name: site.athenaVision.name,
      url: site.founder.url,
      description: site.athenaVision.summary,
      applicationCategory: "BusinessApplication",
      author: { "@id": founderId },
    },
    {
      "@type": "SoftwareApplication",
      "@id": inspecId,
      name: site.inspecTher.name,
      url: site.inspecTher.url,
      description: site.inspecTher.description,
      applicationCategory: "BusinessApplication",
      author: { "@id": founderId },
    },
    {
      "@type": "FAQPage",
      "@id": `${siteUrl}#faq`,
      mainEntity: site.faq.map((item) => ({
        "@type": "Question",
        name: item.q,
        acceptedAnswer: { "@type": "Answer", text: item.a },
      })),
    },
  ],
};

const htmlPath = join(root, "index.html");
const html = readFileSync(htmlPath, "utf8");
const next = html.replace(
  /<script type="application\/ld\+json">\s*[\s\S]*?<\/script>/,
  `<script type="application/ld+json">\n${JSON.stringify(graph)}\n    </script>`,
);
if (!/<script type="application\/ld\+json">/.test(html)) {
  throw new Error("JSON-LD script tag not found in index.html");
}
if (next === html) {
  console.log("json-ld already current");
} else {
  writeFileSync(htmlPath, next);
  console.log("json-ld written");
}
