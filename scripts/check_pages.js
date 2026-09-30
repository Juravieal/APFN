// Syntax-check the inline script of each generated page: node scripts/check_pages.js
const fs = require("fs");
const path = require("path");
const site = path.join(__dirname, "..", "site");
for (const f of ["index.html", "advisor.html", "live.html"]) {
  const file = path.join(site, f);
  if (!fs.existsSync(file)) continue;
  const h = fs.readFileSync(file, "utf8");
  const s = h.slice(h.lastIndexOf("<script>") + 8, h.lastIndexOf("</script>"));
  try {
    new Function(s);
    console.log(f, "script parses,", (h.length / 1024).toFixed(0) + " KB");
  } catch (e) {
    console.log(f, "PARSE ERROR:", e.message);
    process.exitCode = 1;
  }
}
