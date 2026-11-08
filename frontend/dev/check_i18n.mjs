// Checks that translation keys, placeholders, and plural forms are complete.
// Usage: node dev/check_i18n.mjs (from the frontend directory)
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const root = new URL("../js/", import.meta.url).pathname;
const en = (await import(pathToFileURL(join(root, "locales/en.js")))).default;
const pl = (await import(pathToFileURL(join(root, "locales/pl.js")))).default;

function walk(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? (f === "locales" ? [] : walk(p)) : p.endsWith(".js") ? [p] : [];
  });
}
const sources = walk(root).map((p) => [p, readFileSync(p, "utf8")]);
const errors = [];
const PLURAL = { en: ["one", "other"], pl: ["one", "few", "many", "other"] };
const base = (k) => k.replace(/\.(one|few|many|other)$/, "");
const pluralBases = new Set(Object.keys(en).filter((k) => /\.(one|other)$/.test(k) && `${base(k)}.other` in en && `${base(k)}.one` in en).map(base));

// 1) literal keys used in code
const used = new Set();
for (const [file, src] of sources) {
  for (const m of src.matchAll(/\b(t|tn)\(\s*"([^"]+)"/g)) {
    const [, fn, key] = m;
    used.add(key);
    if (fn === "tn") {
      for (const f of PLURAL.en) if (!(`${key}.${f}` in en)) errors.push(`${file}: tn("${key}") misses English form .${f}`);
      for (const f of PLURAL.pl) if (!(`${key}.${f}` in pl)) errors.push(`${file}: tn("${key}") misses Polish form .${f}`);
    } else if (!(key in en)) errors.push(`${file}: t("${key}") is not defined in en.js`);
  }
}
// 2) dynamic key families
const families = {
  "badge.preset.": ["glass", "tint", "frost", "solid", "gradient", "neon", "outline", "custom"], "badge.weight.": ["bold", "regular"],
  "corner.": ["top-left", "top-right", "bottom-left", "bottom-right", "top-center", "bottom-center"],
  "event.": ["queued", "dequeued", "deleted", "delete_failed", "marked", "scheduled", "protected", "unprotected", "postponed", "accelerated", "language_tagged"],
  "interval.": [900, 1800, 3600, 7200, 21600, 43200, 86400], "kind.": ["voiceover", "dub", "audio", "subs"], "ltag.src.": ["tags", "streams", "filename", "files", "episodes"], "reason.": ["watched", "favorited", "in_progress", "protected", "removed_elsewhere", "no_longer_qualifies", "cancelled"],
  "ov.disk.": ["main", "movies", "series"], "source.": ["auto", "manual", "star", "dashboard", "expired"],
};
for (const [prefix, vals] of Object.entries(families)) for (const v of vals) {
  const key = prefix + v; used.add(key);
  if (!(key in en)) errors.push(`dynamic key ${key} missing in en.js`);
  if (prefix === "kind." && !(`${key}.hint` in en)) errors.push(`dynamic key ${key}.hint missing in en.js`);
}
for (const k of ["bulk.kept", "bulk.postponed", "bulk.scheduled", "set.mode", "set.connections", "set.rules", "set.disk", "set.badges", "set.languages", "set.notify", "set.backup", "set.about"]) {
  used.add(k); if (!(k in en)) errors.push(`key ${k} missing in en.js`);
}

// 3) en/pl parity and placeholders
const ph = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
for (const k of Object.keys(en)) {
  if (/\.(one|few|many|other)$/.test(k) && pluralBases.has(base(k))) continue;
  if (!(k in pl)) errors.push(`pl.js misses "${k}"`);
  else if (ph(en[k]) !== ph(pl[k])) errors.push(`placeholders differ for "${k}": en {${ph(en[k])}} vs pl {${ph(pl[k])}}`);
}
for (const k of Object.keys(pl)) if (!(/\.(one|few|many|other)$/.test(k) && pluralBases.has(base(k))) && !(k in en)) errors.push(`en.js misses "${k}"`);
for (const b of pluralBases) for (const f of PLURAL.pl) {
  const k = `${b}.${f}`;
  if (!(k in pl)) errors.push(`pl.js misses plural form "${k}"`);
  else if (ph(pl[k]) !== ph(en[`${b}.other`])) errors.push(`placeholders differ for plural "${b}"`);
}
// 4) unused keys (informational)
const text = sources.map(([, s]) => s).join("\n");
const unused = Object.keys(en).filter((k) => !used.has(base(k)) && !used.has(k) && !text.includes(`"${base(k)}"`) && !k.startsWith("err.") && !k.startsWith("interval."));
if (unused.length) console.log(`note: ${unused.length} keys look unused: ${unused.slice(0, 12).join(", ")}${unused.length > 12 ? ", ..." : ""}`);

if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
console.log(`i18n OK: ${Object.keys(en).length} English / ${Object.keys(pl).length} Polish strings`);
