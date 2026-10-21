// Keep the Python bootstrap definitions identical to the mongosh source.
// node database/export-schema.js [--check]
const fs = require("node:fs");
const path = require("node:path");
const target = path.join(__dirname, "schema-definitions.json");
const text = JSON.stringify(require("./init-mongodb.js"), null, 2) + "\n";
if (process.argv.includes("--check")) {
  if (fs.readFileSync(target, "utf8").replace(/\r\n/g, "\n") !== text) {
    process.stderr.write("schema-definitions.json differs from init-mongodb.js; regenerate it.\n");
    process.exitCode = 1;
  } else {
    process.stdout.write("Schema definitions match.\n");
  }
} else {
  fs.writeFileSync(target, text, "utf8");
  process.stdout.write("Updated schema-definitions.json.\n");
}
